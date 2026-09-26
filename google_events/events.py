"""/google-events/* endpoints: event search (Google's "Events" carousel, 10
per query, optionally with every event's details), event details (single +
batch), a venue's upcoming events, an artist's upcoming dates and search
suggestions.

Cost model (each is one identity use, see google_events/fetch.py):
  search             1 results page (+1-2 when Google leaves the carousel out of a
                     plain phrasing, see FALLBACK_SUFFIXES; +1 viewer per
                     event with include_details)
  details            1 viewer for an event seen in a search within
                     GOOGLE_EVENTS_EVENT_MEMO_DAYS (or passed as a token);
                     else 1 results page for the search the event came from
                     (its `link`) + 1 viewer
  venue / artist     1 results page (the panel carries every ticket seller;
                     +1 when the first phrasing shows no panel)
  autocomplete       open /complete/search, no identity
"""
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.parse import quote_plus, urlencode

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from google_events import fetch, panels, parsers as P  # noqa: E402
from urllib.parse import parse_qsl, unquote, urlparse, urlunparse  # noqa: E402
from google_search import autocomplete as gs_autocomplete  # noqa: E402
from google_search.serp import resolve_goto  # noqa: E402

MAX_EVENTS = 10          # Google's carousel holds 10 events
DATES = ["any", "today", "tomorrow", "this_week", "this_weekend", "next_week", "this_month", "next_month"]
DATE_ALIASES = {"week": "this_week", "weekend": "this_weekend", "month": "this_month"}
# Google reads the date filter from the query wording ("… this weekend"),
# which the carousel honours (verified 2026-09-26: "comedy shows in chicago
# today" -> only today's shows; "events in austin next month" -> October).
DATE_WORDS = {"today": "today", "tomorrow": "tomorrow", "this_week": "this week", "this_weekend": "this weekend",
              "next_week": "next week", "this_month": "this month", "next_month": "next month"}
FALLBACK_SUFFIXES = ("coming up", "this year")
# Venue panel triggers, tried in order: "<venue> upcoming events" lists 20
# where the bare name lists 3; some venues answer only "<venue> events"
# ("The O2 London upcoming events": no panel, "the o2 london events": 13).
VENUE_PHRASES = ("{} upcoming events", "{} events")
# Artist panel triggers, tried in order: "<artist> tickets" lists the tour;
# on a miss (1 of 6 lookups on one identity, 2026-09-26) "<artist> concerts"
# shows the same panel. With a place: "<artist> <place>" lists nearby dates.
ARTIST_PHRASES = ("{artist} tickets", "{artist} concerts")
ARTIST_NEAR_PHRASES = ("{artist} {location}", "{artist} concerts {location}")
NS_EVENT = "google-events.memo.event"
_memo_writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="google-events-memo")
_EVENT_WORDS_RE = re.compile(r"\b(events?|concerts?|shows?|festivals?|gigs?|things to do|comedy|theat(?:er|re)|"
                             r"games?|exhibitions?|workshops?|parties|party|meetups?|classes|tours?|fairs?|"
                             r"markets?|performances?|musicals?|plays?|nightlife|webinars?)\b", re.I)


# ---- query assembly -------------------------------------------------------------------------------------

def build_query(query, location=None, date=None, online_only=None):
    """The query Google is sent: the search, 'in <location>', the date
    wording, and 'online' when only online events are wanted. Location goes
    into the words: uule is ignored by the carousel ("events near me" with a
    Seattle uule listed events near the exit IP, 2026-09-26)."""
    q = query.strip()
    if online_only and not re.search(r"\b(online|virtual)\b", q, re.I):
        q = f"online {q}"
    if location and location.lower() not in q.lower():
        q = f"{q} in {location}"
    if date and date != "any":
        words = DATE_WORDS[date]
        if words not in q.lower():
            q = f"{q} {words}"
    return q


def results_link(query, country, language):
    return f"{P.SITE}/search?" + urlencode({"q": query, "hl": language, "gl": country.lower()}, quote_via=quote_plus)


def clean_link(link):
    """Drop the tracking Google's ticket feeds append (utm_*, gclid /
    gclsrc, unfilled ad-macro placeholders like {GOOGLE-ADS-CLICK-SOURCE}),
    keeping the seller's own params."""
    if not link:
        return None
    parts = urlparse(link)
    if not parts.query:
        return link
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not (k.lower().startswith("utm_") or k.lower() in ("gclid", "gclsrc", "dclid")
                    or "{" in unquote(k) or "{" in unquote(v))]
    return urlunparse(parts._replace(query=urlencode(kept)))


# ---- memo -------------------------------------------------------------------------------------------------

def _card_memo(event, token, query, country, language):
    """What the memo keeps per event: the viewer token and the card fields
    the viewer does not repeat."""
    thumb = event["thumbnail"] if (event["thumbnail"] or "").startswith("http") else None
    return {"token": token, "query": query, "country": country, "language": language, "title": event["title"],
            "type": event["type"], "locality": event["venue"]["locality"], "thumbnail": thumb,
            "start_time": event["schedule"]["start_time"]}


def _remember(parsed, query, country, language):
    """event id -> card memo (see _card_memo), written in the background.
    Returns {event id: card memo} for the caller's own use."""
    ttl = timedelta(days=config.GOOGLE_EVENTS_EVENT_MEMO_DAYS)
    cards = {e["id"]: _card_memo(e, parsed["tokens"][e["id"]], query, country, language)
             for e in parsed["events"] if e["id"] and parsed["tokens"].get(e["id"])}

    def flush():
        for event_id, value in cards.items():
            fetch.memo.put(NS_EVENT, {"id": event_id}, value, ttl)
    _memo_writer.submit(flush)
    return cards


# ---- details -----------------------------------------------------------------------------------------------

def _resolve_links(event):
    """Ticket links arrive as /goto?url= wrappers: resolve each to the
    seller's page (open redirect, no identity), tracking params stripped."""
    items = event.get("ticket_and_info_links") or []
    wrapped = list({i["link"] for i in items if i.get("link") and "/goto?" in i["link"]})
    resolved = dict(zip(wrapped, fetch.run_parallel_quiet([lambda link=link: resolve_goto(link) for link in wrapped],
                                                          workers=8))) if wrapped else {}
    for i, item in enumerate(items):
        target = resolved.get(item["link"])
        link = clean_link(target) if target and "/goto?" not in target else item["link"]
        items[i] = {"source": item["source"], "link": link,
                    "domain": P.domain_of(link) if "/goto?" not in (link or "") else None,
                    "is_official_seller": item["is_official_seller"], "min_price": item["min_price"],
                    "currency": item["currency"]}
    source = event.get("source") or {}
    for key in ("link", "description_link"):
        if source.get(key):
            source[key] = clean_link(source[key])
    for image in event.get("images") or []:
        if image.get("source_link"):
            image["source_link"] = clean_link(image["source_link"])
    return event


def _finish(out, ref, card, country, language):
    """Merge what the card knew (type, locality, first-seen title) and build
    the event's link."""
    card = card or {}
    out["id"] = out.get("id") or ref.get("id")
    out["type"] = card.get("type")
    out["venue"]["locality"] = card.get("locality")
    if not out.get("thumbnail") and card.get("thumbnail"):
        out["thumbnail"] = card["thumbnail"]
    # a card without a time ("Sep 28") has none: the viewer's 12:00 AM is filler
    if card and card.get("start_time") is None and out["schedule"].get("start_time") == "00:00":
        for key in ("start_time", "start_time_utc", "end_time", "end_time_utc"):
            out["schedule"][key] = None
    out["link"] = P.event_link(out["id"], card.get("query") or ref.get("query"), card.get("country") or country,
                               card.get("language") or language)
    ordered = {k: out[k] for k in ("id", "title", "link", "type", "description", "schedule", "venue",
                                   "ticket_and_info_links", "images", "thumbnail", "map_image", "source",
                                   "google_calendar_link")}
    return _resolve_links(ordered)


def _from_token(token, country, language):
    text = fetch.viewer(token, country=country, language=language)
    return P.parse_details(text, country=country, language=language) if text else None


def details(event, country="US", language="en", _card=None):
    """One event's full record. `event` is {"id", "token", "query"} (schemas)."""
    event_id = event.get("id")
    card = _card or (fetch.memo.get(NS_EVENT, {"id": event_id}) if event_id else None)
    token = event.get("token") or (card or {}).get("token")
    out = _from_token(token, country, language) if token else None
    if out is not None and event_id and out.get("id") and out["id"] != event_id:
        out = None
    if out is None:
        query = event.get("query") or (card or {}).get("query")
        if not event_id or not query:
            raise fetch.GoogleNotFound(
                f"event {event_id or '(token)'} is unknown here or its token expired: pass the event's `link` from "
                f"/google-events/search (it carries the search the event came from), or search again")
        c, l = (card or {}).get("country") or country, (card or {}).get("language") or language
        parsed = P.parse_results(fetch.results_page(query, country=c, language=l, label="event"), country=c, language=l)
        cards = _remember(parsed, query, c, l)
        token = parsed["tokens"].get(event_id)
        if not token:
            raise fetch.GoogleNotFound(f"Google no longer lists event {event_id} for the search '{query}' (it ended, "
                                       f"or the carousel moved on): search again for a current id")
        card = cards.get(event_id) or card
        out = _from_token(token, c, l)
        if out is None:
            raise fetch.GoogleUpstreamError(f"Google's event viewer answered nothing for {event_id}")
    return _finish(out, event, card, country, language)


def batch(events, country="US", language="en"):
    """Up to 10 events in parallel; ids Google no longer resolves are listed
    in not_found, other failures in failed."""
    errors = {}

    def label(ref):
        return ref.get("id") or (ref.get("token") or "")[:24] + "…"

    def one(ref):
        def call():
            try:
                return details(ref, country=country, language=language)
            except fetch.GoogleNotFound:
                return None
            except Exception as e:
                errors[label(ref)] = e
                return None
        return call
    with ThreadPoolExecutor(max_workers=min(config.GOOGLE_EVENTS_BATCH_WORKERS, len(events))) as pool:
        results = list(pool.map(lambda f: f(), [one(e) for e in events]))
    if errors and len(errors) == len(events):
        raise next(iter(errors.values()))
    return {"events": [r for r in results if r],
            "not_found": [label(e) for e, r in zip(events, results) if not r and label(e) not in errors],
            "failed": [{"id": k, "error": f"{type(e).__name__}: {e}"} for k, e in errors.items()]}


# ---- search ------------------------------------------------------------------------------------------------

def search(query, location=None, date="any", online_only=None, include_details=False, country="US", language="en"):
    """Google's Events carousel for a query: up to 10 events (Google shows
    no more), each with details when include_details is set."""
    q = build_query(query, location, date, online_only)
    html = fetch.results_page(q, country=country, language=language)
    parsed = P.parse_results(html, country=country, language=language)
    if not parsed["events"] and (date or "any") == "any":
        # Google leaves the carousel out of some plain phrasings ("events in
        # austin", "comedy shows chicago": no carousel on 4/4 fresh identities
        # in 4 states, 2026-09-26) yet shows it once the words carry a time
        # ("… coming up": the next ~2 weeks, the widest time-independent
        # range; "… this year"). Retry with those before answering empty.
        for suffix in FALLBACK_SUFFIXES:
            retry = f"{q} {suffix}"
            html = fetch.results_page(retry, country=country, language=language, label="search-retry")
            parsed = P.parse_results(html, country=country, language=language)
            if parsed["events"]:
                q = retry
                break
    if not parsed["events"]:
        fetch.gfetch.dump_debug("events_no_carousel", html)
    cards = _remember(parsed, q, country, language)
    events = parsed["events"][:MAX_EVENTS]
    if include_details and events:
        refs = [({"id": e["id"], "token": parsed["tokens"].get(e["id"]), "query": q}, cards.get(e["id"])) for e in events]
        full = fetch.run_parallel_quiet([lambda r=r: details(r[0], country=country, language=language, _card=r[1])
                                         for r in refs])
        events = [dict(f, position=e["position"]) if f else e for e, f in zip(events, full)]
        events = [{"position": e["position"], **{k: v for k, v in e.items() if k != "position"}} for e in events]
    return {"search_information": {"query": q, "link": results_link(q, country, language)},
            "count": len(events), "events": events}


# ---- venue / artist ---------------------------------------------------------------------------------------

def _panel_result(queries, kind, country, language, not_found):
    """The first of `queries` whose results page carries an events panel ->
    (query, panel); GoogleNotFound when none does."""
    for q in queries:
        html = fetch.results_page(q, country=country, language=language, label=kind)
        panel = panels.parse_panel(html, country=country)
        if panel is not None:
            for event in panel["events"]:
                _resolve_links(event)
            return q, panel
        fetch.gfetch.dump_debug(f"events_no_{kind}_panel", html)
    raise fetch.GoogleNotFound(not_found)


def venue_events(venue, location=None, country="US", language="en"):
    """A venue's upcoming events (Google's "<venue> / Events" panel, up to 20:
    "<venue> upcoming events" — the bare name shows 3): title, date and
    time, starting price and every ticket seller."""
    name = venue if not location or location.lower() in venue.lower() else f"{venue} {location}"
    q, panel = _panel_result(
        [p.format(name) for p in VENUE_PHRASES], "venue", country, language,
        f"Google shows no events panel for the venue '{name}': check the name (add the city for common names); "
        f"Google lists events only for venues it knows")
    return {"search_information": {"query": q, "link": results_link(q, country, language)},
            "venue": {"name": panel["name"], "link": results_link(panel["name"], country, language)},
            "count": len(panel["events"]), "events": panel["events"]}


def artist_events(artist, location=None, country="US", language="en"):
    """An artist's upcoming dates (Google's "<artist> / Events" panel):
    "<artist> tickets" lists the tour (up to 20), "<artist> <place>" the
    dates near that place. Date, time, city, venue, starting price and every
    ticket seller per date."""
    q, panel = _panel_result(
        [p.format(artist=artist, location=location) for p in (ARTIST_NEAR_PHRASES if location else ARTIST_PHRASES)],
        "artist", country, language,
        f"Google shows no upcoming dates for '{artist}'" + (f" near {location}" if location else "")
        + ": the artist may not be touring" + (" there" if location else ""))
    return {"search_information": {"query": q, "link": results_link(q, country, language)},
            "artist": {"name": panel["name"], "link": results_link(panel["name"], country, language)},
            # without a location Google's header says "Near you" (the exit IP): not a place
            "near": panel["near"] if location else None, "count": len(panel["events"]), "events": panel["events"]}


# ---- suggestions ----------------------------------------------------------------------------------------------

def autocomplete(query, country="US", language="en", limit=10):
    """Event-search suggestions for a partial query ("concerts in aus" ->
    "concerts in austin this weekend", …); a bare place or name gets
    " events" so the suggestions stay about events."""
    prefix = query if _EVENT_WORDS_RE.search(query) else f"{query} events"
    raw = gs_autocomplete.autocomplete(prefix, client="google", country=country, language=language, limit=20)
    items = []
    for s in raw["suggestions"]:
        value = s.get("text")
        if s.get("type") == "query" and value and value not in items:     # not AI-mode prompts / entities
            items.append(value)
    items = items[:limit]
    return {"query": query, "count": len(items),
            "suggestions": [{"position": i + 1, "text": t, "link": results_link(t, country, language)}
                            for i, t in enumerate(items)]}

