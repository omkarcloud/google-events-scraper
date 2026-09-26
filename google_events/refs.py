"""Reference parsing: ONE param per input that auto-detects its forms
(tripadvisor QueryOrLinkField convention — never a sibling `url` / `id`
pair). Resolvers return plain JSON values so the validated params stay
usable as a response-cache key.

  query   "concerts in austin" | any google.<tld>/search link (its q, and gl
          / hl through the schema) — shared with google_search.refs
  event   an event id: the Knowledge Graph MID search returns ("/g/11zf7d8nkg")
        | the `link` of a search result (…/search?q=…#htidocid=/g/…)
        | an event token: Google's viewer token (data-async-fc, "EswHCowH…"),
          any Google URL carrying it as fc= (a Maps directions or callback
          link), or the base64 {"fc": …, "e": …} event_id other Google Events
          APIs return
          -> {"id": MID or None, "token": viewer token or None, "query": q or None}
  venue / artist   a name | a google.<tld>/search link whose q names it
"""
import base64
import json
import re
from urllib.parse import parse_qs, unquote, urlparse

from google_search.refs import is_google_link, params_of  # noqa: F401 (params_of re-exported for schemas)

_MID_RE = re.compile(r"^/[gm]/[0-9a-z_]{2,20}$", re.I)
_MID_IN_RE = re.compile(r"(?:htidocid=|kgmid=|[#&?]mid=)(%2F|/)([gm])(%2F|/)([0-9a-z_]{2,20})", re.I)
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{120,}={0,2}$")
_LEGACY = "/authority/horizon/"


def is_event_id(value):
    return bool(_MID_RE.match(unquote(value or "").strip()))


def _b64(value):
    value = value.strip()
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError):
        return None


def _looks_like_token(value):
    """Google's viewer token: base64url of a protobuf whose first field is
    tag 2, length-delimited (0x12) — "Es…" in text."""
    if not _TOKEN_RE.match(value):
        return False
    raw = _b64(value)
    return bool(raw) and raw[:1] == b"\x12"


def _from_event_id_blob(value):
    """The base64 JSON event_id of other Google Events APIs:
    {"fc": "<viewer token>", "e": "/g/…"} -> ref, else None."""
    raw = _b64(value)
    if not raw or not raw.lstrip().startswith(b"{"):
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("fc"), str):
        return None
    mid = data.get("e") if is_event_id(data.get("e")) else None
    return {"id": mid, "token": data["fc"], "query": None}


def resolve_event(value):
    """An event id / event link / event token -> {"id", "token", "query"}."""
    raw = (value or "").strip()
    if not raw:
        raise ValueError("event must not be empty")
    if is_event_id(raw):
        return {"id": unquote(raw), "token": None, "query": None}
    if raw.lower().startswith(("http://", "https://", "//")) or "google." in raw.lower():
        if not is_google_link(raw):
            raise ValueError("event must be an event id, a Google Events link or an event token")
        parts = urlparse(raw if "://" in raw else "https://" + raw.lstrip("/"))
        params = {k: v[0] for k, v in parse_qs(parts.query).items() if v}
        fragment = {k: v[0] for k, v in parse_qs(parts.fragment).items() if v}
        token = params.get("fc") or fragment.get("fc")
        m = _MID_IN_RE.search(raw)
        mid = f"/{m.group(2).lower()}/{m.group(4)}" if m else None
        if token:
            return {"id": mid, "token": token, "query": None}
        if mid:
            return {"id": mid, "token": None, "query": (params.get("q") or "").strip() or None}
        legacy = unquote(params.get("htidocid") or fragment.get("htidocid") or "")
        if legacy and _LEGACY in (_b64(legacy) or b"").decode("utf-8", "ignore"):
            raise ValueError("this link is from Google's retired Events page (ibp=htl;events), whose ids no longer "
                             "resolve: search again and use the new event's id or link")
        raise ValueError("a Google link must carry the event: use the `link` of a /google-events/search result")
    if _looks_like_token(raw):
        return {"id": None, "token": raw, "query": None}
    blob = _from_event_id_blob(raw)
    if blob:
        return blob
    decoded = (_b64(raw) or b"").decode("utf-8", "ignore")
    if _LEGACY in decoded:
        raise ValueError("this event_id is from Google's retired Events page, whose ids no longer resolve: search "
                         "again and use the new event's id")
    raise ValueError("event must be an event id (like /g/11zf7d8nkg), the `link` of a search result, or an event token")


def split_events(value):
    """'id1, id2, <link>' -> items (links and tokens carry no commas)."""
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def resolve_name(value, what):
    """A venue / artist name, or a google.com/search link whose q names it."""
    raw = (value or "").strip()
    if is_google_link(raw):
        q = params_of(raw).get("q")
        if not q:
            raise ValueError(f"a Google link must carry a q= naming the {what}")
        raw = q
    elif raw.lower().startswith(("http://", "https://", "//")):
        raise ValueError(f"{what} must be a name or a google.com/search link")
    raw = re.sub(r"\s+", " ", raw).strip()
    if not raw:
        raise ValueError(f"{what} must not be empty")
    return raw
