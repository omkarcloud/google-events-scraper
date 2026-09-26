"""Google Jobs transport.

What answers what (verified 2026-09-26; research 2026-09-24):

  * RESULTS PAGE  www.google.com/search?q&udm=8&hl&gl[&lrad][&htidocid]
    — botguard-walled for plain curl_cffi on every impersonation / exit
    (the ~92 KB "enable JavaScript" page, also for the legacy
    ibp=htl;jobs URL). Served through a minted SERP identity: the same
    pool as /google-search/* (google_search/identity.py — headed Camoufox
    + CapSolver mints cookies on a sticky residential exit, curl_cffi
    replays them through that exit). 10 jobs per page, each card carrying
    the full viewer (description, highlights, apply links).
  * NEXT PAGE     /async/callback:550?fc=<token>&fcv=3&async=_fmt:prog —
    the infinite-scroll loader: 10 more jobs + the next token. `start=`
    on udm=8 answers the wall (and burns the identity), so this chain is
    the only pagination.
  * JOB VIEWER    /async/callback:8166?fc=<card token>&fcv=3&hl&gl&
    async=_fmt:prog — the viewer panel of one job (employer ratings,
    website) in the chosen language.
  Both callbacks need identity cookies: without them (or with cookies
  replayed from another exit) Google answers a 59-byte stub whose status
  block is [3,0,1,1,0]. The tokens themselves are NOT identity- or
  session-bound: 2-day-old tokens minted by another identity still served
  (2026-09-26), so they are memoised (Memo below) and reused.
  * SUGGESTIONS   /complete/search — open (plain curl_cffi, no cookies);
    google_search/autocomplete.py does the call.

Filters: Google's own filter links append words to the query ("… full
time", "… in the last week", "… remote", "… no degree") plus a `uds`
token; the token alone changes nothing and the words alone give the same
results (tested with page-own, foreign and no token, 2026-09-26), so a
filter is the query suffix only.
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cache  # noqa: E402
import config  # noqa: E402
from google_jobs import markup  # noqa: E402
from google_search import fetch as gfetch, identity  # noqa: E402

GoogleBlocked = gfetch.GoogleBlocked
GoogleUpstreamError = gfetch.GoogleUpstreamError
GoogleNotFound = gfetch.GoogleNotFound
GoogleBadRequest = gfetch.GoogleBadRequest

NEXT_PAGE = 550
VIEWER = 8166
CALLBACK_HEADERS = {"accept": "*/*", "sec-fetch-dest": "empty", "sec-fetch-mode": "cors", "sec-fetch-site": "same-origin"}

identity.start_keeper()   # the SERP identity pool is shared with /google-search/*


# ---- memo: tokens and job references ---------------------------------------------------------------

class _SqliteStore:
    """The memo's durable layer when the response-cache database is off (the
    Windows VPS has no Postgres): one SQLite file, rows expire by
    `expires_at`. Same get / put shape as cache.PostgresResponseCache."""

    def __init__(self, path):
        import sqlite3
        self._path = path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=10)
        with self._lock:
            self._conn.execute("CREATE TABLE IF NOT EXISTS memo (ns TEXT, k TEXT, v TEXT, expires_at REAL, "
                               "PRIMARY KEY (ns, k))")
            self._conn.execute("DELETE FROM memo WHERE expires_at < ?", (time.time(),))
            self._conn.commit()

    def get(self, namespace, key):
        import json
        with self._lock:
            row = self._conn.execute("SELECT v FROM memo WHERE ns = ? AND k = ? AND expires_at > ?",
                                     (namespace, cache.PostgresResponseCache.canonical(key), time.time())).fetchone()
        return {"data": json.loads(row[0])} if row else None

    def put(self, namespace, key, value, ttl):
        import json
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO memo VALUES (?, ?, ?, ?)",
                               (namespace, cache.PostgresResponseCache.canonical(key), json.dumps(value),
                                time.time() + ttl.total_seconds()))
            self._conn.commit()


class Memo:
    """Small key -> value store with a TTL: the response-cache database
    when caching is on (survives restarts, shared across replicas), else a
    local SQLite file (config.GOOGLE_MEMO_SQLITE_PATH; survives restarts),
    with an in-process dict in front. Holds job id -> {query, token, …},
    results-page -> next-page token and event id -> viewer token, so details
    and deep pages skip re-walking Google."""

    def __init__(self, max_local=20000):
        self._local = {}
        self._lock = threading.Lock()
        self._max = max_local
        self._sqlite = None
        self._sqlite_failed = False

    def _store(self):
        store = cache.storage()
        if store is not None:
            return store
        path = getattr(config, "GOOGLE_MEMO_SQLITE_PATH", None)
        if not path or self._sqlite_failed:
            return None
        if self._sqlite is None:
            with self._lock:
                if self._sqlite is None:
                    try:
                        self._sqlite = _SqliteStore(path)
                    except Exception as e:
                        self._sqlite_failed = True
                        print(f"google-jobs: memo sqlite unavailable ({path}): {type(e).__name__}: {e}")
                        return None
        return self._sqlite

    def get(self, namespace, key):
        with self._lock:
            hit = self._local.get((namespace, cache.PostgresResponseCache.canonical(key)))
        if hit and hit[0] > time.time():
            return hit[1]
        store = self._store()
        if store is None:
            return None
        try:
            row = store.get(namespace, key)
        except Exception as e:
            print(f"google-jobs: memo read failed ({namespace}): {type(e).__name__}: {e}")
            return None
        return row["data"] if row else None

    def put(self, namespace, key, value, ttl):
        with self._lock:
            if len(self._local) >= self._max:
                now = time.time()
                for k in [k for k, v in self._local.items() if v[0] <= now] or list(self._local)[: self._max // 4]:
                    self._local.pop(k, None)
            self._local[(namespace, cache.PostgresResponseCache.canonical(key))] = (time.time() + ttl.total_seconds(), value)
        store = self._store()
        if store is None:
            return
        try:
            store.put(namespace, key, value, ttl)
        except Exception as e:
            print(f"google-jobs: memo write failed ({namespace}): {type(e).__name__}: {e}")


memo = Memo()


# ---- requests -----------------------------------------------------------------------------------------

def results_page(query, *, country, language, radius=None, job_id=None, label="search"):
    """One udm=8 results page (HTML) through a SERP identity."""
    params = {"q": query, "hl": language, "gl": (country or "us").lower(), "udm": 8}
    if radius:
        params["lrad"] = radius
    if job_id:
        params["htidocid"] = job_id
    html, _ = identity.serp_html(params, label=f"jobs-{label}")
    return html


def _callback_verdict(resp):
    """'ok' for a served chunk or a token error (HTTP 4xx/5xx other than a
    block — the caller reads it); 'sorry' / 'refused' when Google refused
    the identity itself."""
    if resp.status_code in (403, 429) or "/sorry/" in str(resp.url):
        return "sorry"
    if markup.is_refused(resp.text):
        return "refused"
    return "ok"


def callback(ons, token, *, country, language, label):
    """An async callback answer (text), or None when Google rejected the
    token (HTTP 4xx/5xx: expired / malformed)."""
    params = {"fc": token, "fcv": 3, "hl": language, "gl": (country or "us").lower(), "udm": 8, "async": "_fmt:prog"}
    resp = identity.replay(f"/async/callback:{ons}", params, label=f"jobs-{label}", verdict=_callback_verdict,
                           headers=CALLBACK_HEADERS)
    if resp.status_code >= 400:
        gfetch.dump_debug(f"jobs_{label}_{resp.status_code}", resp.text)
        return None
    return resp.text


def next_page(token, *, country, language):
    return callback(NEXT_PAGE, token, country=country, language=language, label="next-page")


def viewer(token, *, country, language):
    return callback(VIEWER, token, country=country, language=language, label="viewer")


def run_parallel_quiet(fns, workers=None):
    return gfetch.run_parallel_quiet(fns, workers=workers or config.GOOGLE_JOBS_BATCH_WORKERS)
