"""Google Events transport.

What answers what (verified 2026-09-24/26):

  * EVENTS PAGE   www.google.com/search?q&hl&gl&ibp=htl;events — RETIRED by
    Google in Aug 2026: a JS shell that renders "Can't find events that
    match" for every query and country.
  * RESULTS PAGE  www.google.com/search?q&hl&gl[&uule] — the "Events"
    carousel (10 cards) for event-shaped queries ("events in austin",
    "comedy shows in chicago today", "concerts in new york this weekend"),
    and an "<artist | venue> / Events" panel (tour dates / a venue's next
    events with ticket sellers and prices) for artist and venue queries.
    Botguard-walled for plain curl_cffi on every impersonation / exit (the
    ~92 KB "enable JavaScript" page), so it is served through a minted SERP
    identity: the same pool as /google-search/* and /google-jobs/*
    (google_search/identity.py — headed Camoufox + CapSolver mint cookies
    on a sticky residential exit, curl_cffi replays them through that exit).
  * EVENT VIEWER  /async/callback:8243?fc=<card token>&fcv=3&hl&gl&
    async=_fmt:prog — one event's full record (schedule with time zone,
    address, ticket sellers with prices, description, venue). Needs
    identity cookies: without them Google answers a 59-byte stub whose
    status block is [3,0,1,1,0]. The tokens themselves are NOT identity- or
    session-bound: a 2-day-old token minted by another identity served
    (2026-09-26), so event id -> token is memoised (google_jobs.fetch.Memo).
  * OPEN (no identity)  /complete/search suggestions; the /goto?url=
    redirects behind ticket links (google_search.serp.resolve_goto). The
    Maps search (tbm=map) answers plain curl too, but lists venues, not
    events.

Coverage (2026-09-26): the carousel shows for US / GB / CA / AU / IN searches
in English; German, French and Spanish queries, and "online events" style
queries, return no carousel. Whether a phrasing gets one shifts over the
day and is the same for every identity: by evening "events in austin" had
none on 4/4 fresh identities while "events in austin this week / coming up
/ this year" and "concerts in austin" had 10, so search retries a plain
phrasing with a time word (events.FALLBACK_SUFFIXES). A Knowledge Graph MID alone cannot be looked up
(kgmid= opens the artist, not the event), which is why details need a
memoised or supplied token.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from google_events import markup  # noqa: E402
from google_jobs.fetch import Memo  # noqa: E402  (also starts the shared identity keeper)
from google_search import fetch as gfetch, identity  # noqa: E402

GoogleBlocked = gfetch.GoogleBlocked
GoogleUpstreamError = gfetch.GoogleUpstreamError
GoogleNotFound = gfetch.GoogleNotFound
GoogleBadRequest = gfetch.GoogleBadRequest

VIEWER = 8243
CALLBACK_HEADERS = {"accept": "*/*", "sec-fetch-dest": "empty", "sec-fetch-mode": "cors", "sec-fetch-site": "same-origin"}

memo = Memo()


def results_page(query, *, country, language, uule=None, label="search"):
    """One results page (HTML) through a SERP identity."""
    params = {"q": query, "hl": language, "gl": (country or "us").lower()}
    if uule:
        params["uule"] = uule
    html, _ = identity.serp_html(params, label=f"events-{label}")
    return html


def _callback_verdict(resp):
    """'ok' for a served chunk or a token error (HTTP 4xx/5xx — the caller
    reads it); 'sorry' / 'refused' when Google refused the identity."""
    if resp.status_code in (403, 429) or "/sorry/" in str(resp.url):
        return "sorry"
    if markup.is_refused(resp.text):
        return "refused"
    return "ok"


def viewer(token, *, country, language):
    """The event viewer chunk (text), or None when Google rejected the token
    (HTTP 4xx/5xx: expired / malformed)."""
    params = {"fc": token, "fcv": 3, "hl": language, "gl": (country or "us").lower(), "async": "_fmt:prog"}
    resp = identity.replay(f"/async/callback:{VIEWER}", params, label="events-viewer", verdict=_callback_verdict,
                           headers=CALLBACK_HEADERS)
    if resp.status_code >= 400:
        gfetch.dump_debug(f"events_viewer_{resp.status_code}", resp.text)
        return None
    return resp.text


def run_parallel_quiet(fns, workers=None):
    return gfetch.run_parallel_quiet(fns, workers=workers or config.GOOGLE_EVENTS_BATCH_WORKERS)
