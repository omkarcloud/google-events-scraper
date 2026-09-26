"""Parsers for Google Events: the "Events" carousel of a results page and
the event viewer (/async/callback:8243). Verified 2026-09-26 against
captured pages (google_events/fixtures).

Carousel card (one per event, 10 per page):
  div[data-ssid="event_viewer_entrypoint"][data-async-fc=<viewer token>]
  [id=<event Knowledge Graph MID>]
    img            thumbnail (inline data URI, or a lazy URL in google.ldi)
    date chip      "Today" / "Wed, Sep 30" (presentation only)
    [role=heading] title
    rows .qXy4ec   [type] · <calendar icon> "Sep 30, 7:00 PM" ·
                   <pin icon> venue · locality ("Austin, TX"); the type row
                   is optional, so rows are told apart by their icon.

Viewer (a callback chunk: HTML frame + JS data):
  HTML  h3 title · a.fuxI date line · .C1cKff address · action bar .lwcikd
        (Website / Calendar / Directions / tel: Call) · "Tickets &
        information" a.Zxvcad (source .W0LCVe, "Official seller" badge
        .Pt08Vc, "from $30" .gSMDJ) · About .rUh2g (+ "Source ›" a.Vovx8b) ·
        Venue block (.A5yTVb description)
  JS    {"193": [[venue feature id, event MID, 16]], "194": [[16, title,
        event MID]]} + [.., title, .., [[image, .., .., page, domain],
        [static map]]] · {"498469725": [venue name, [rating, .., ..,
        [reviews]], .., [[.., [venue type]]], .. , [venue MID, …]]} ·
        "/search?…&ludocid=<venue cid>…" · the Google Calendar template
        link (dates=<local start>/<local end>&ctz=<IANA zone>) · the Maps
        directions link (daddr=<street address>).
Labels are localized with hl=; icons, hooks and the JS data are not.
"""
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, quote_plus, urlencode, urlparse

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

from google_events import markup
from google_search.serp_parsers import deferred_images, first, image_src, text
from google_search.shared import clean_text, to_int
from google_shopping.parsers import money

SITE = "https://www.google.com"
ICON_DATE = "M17 12h-5v5h5v-5zM16 1v2H8V1H6"       # calendar icon: the date / time row
ICON_PLACE = "M12 2C8.13 2 5 5.13 5 9c0 5.25"      # pin icon: the venue row
_MID_RE = re.compile(r"^/[gm]/[0-9a-z_]{2,20}$", re.I)

# Month words of the card date ("Sep 30", "26 Sept", "3 oct.", "12. Okt.") by
# prefix, longest first; the carousel is localized with hl=.
_MONTHS = sorted({
    "january": 1, "jan": 1, "jän": 1, "janv": 1, "ene": 1, "gen": 1,
    "february": 2, "feb": 2, "févr": 2, "fev": 2, "fév": 2,
    "march": 3, "mar": 3, "mär": 3, "mars": 3, "mrt": 3,
    "april": 4, "apr": 4, "avr": 4, "abr": 4,
    "may": 5, "mai": 5, "mag": 5, "mei": 5,
    "june": 6, "jun": 6, "juin": 6, "giu": 6,
    "july": 7, "jul": 7, "juil": 7, "lug": 7,
    "august": 8, "aug": 8, "août": 8, "aout": 8, "ago": 8,
    "september": 9, "sep": 9, "sept": 9, "set": 9,
    "october": 10, "oct": 10, "okt": 10, "ott": 10, "out": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12, "dez": 12, "déc": 12, "dic": 12,
}.items(), key=lambda kv: -len(kv[0]))
_TIME_RE = re.compile(r"(?<!\d)(\d{1,2})(?:[:.h](\d{2}))?\s*(a\.?\s?m\.?|p\.?\s?m\.?)?(?![\d])", re.I)
_CAL_DATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})(Z)?)?$")


# ---- values ----------------------------------------------------------------------------------------------

def _month_of(word):
    word = word.lower().strip(".")
    for prefix, month in _MONTHS:
        if word.startswith(prefix) and (len(word) <= len(prefix) + 6):
            return month
    return None


_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _weekday_of(text):
    """The weekday a date text names ("Sat, Sep 26" / "Thu 7:00 PM"), or None."""
    for word in re.findall(r"[^\W\d_]{3,}", text or ""):
        wd = _WEEKDAYS.get(word.lower()[:3])
        if wd is not None and word.lower() in ("mon", "tue", "tues", "wed", "thu", "thur", "thurs", "fri", "sat", "sun",
                                               "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
                                               "sunday"):
            return wd
    return None


def _infer_year(month, day, now, weekday=None):
    """Carousel dates carry no year: the next occurrence of month/day (a
    date a month or more in the past means next year). A weekday in the text
    decides instead when it matches exactly one nearby year — Google's
    panels sometimes still list an event that already happened
    ("Thu, Aug 6" seen on 2026-09-26 is Aug 6 2026, not 2027)."""
    today = now.date()
    if weekday is not None:
        hits = []
        for year in (today.year - 1, today.year, today.year + 1):
            try:
                candidate = date(year, month, day)
            except ValueError:
                continue
            if candidate.weekday() == weekday:
                hits.append(candidate)
        if len(hits) == 1:
            return hits[0]
    for year in (today.year, today.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if candidate >= today - timedelta(days=31):
            return candidate
    return None


def parse_card_datetime(value, now=None):
    """'Sep 30, 7:00 PM' / '26 Sept, 19:30' / 'Sat, Sep 26, 8:00 AM' /
    'Sep 28' -> ('2026-09-30', '19:00' or None). (None, None) when there is
    no date. The year is inferred (see _infer_year) unless the text has one."""
    raw = clean_text(value)
    if not raw:
        return None, None
    now = now or datetime.now(timezone.utc)
    raw = raw.replace(" ", " ").replace(" ", " ")
    for m in re.finditer(r"[^\W\d_]{3,}\.?", raw):
        month = _month_of(m.group(0))
        if not month:
            continue
        after = re.match(r"\s*(\d{1,2})(?!\d|[:.]\d)", raw[m.end():])
        before = re.search(r"(?<![\d:.])(\d{1,2})\.?\s*$", raw[:m.start()])
        hit = after or before
        if not hit:
            continue
        day = int(hit.group(1))
        span = (m.end(), m.end() + hit.end()) if after else (before.start(), m.end())
        rest = raw[:span[0]] + " " + raw[span[1]:]
        year = re.search(r"\b(20\d\d)\b", rest)
        if year:
            rest = rest.replace(year.group(1), " ")
            try:
                when = date(int(year.group(1)), month, day)
            except ValueError:
                when = None
        else:
            when = _infer_year(month, day, now, _weekday_of(raw))
        return (when.isoformat() if when else None), parse_clock(rest)
    return None, None


def parse_clock(value):
    """'7:00 PM' / '19:30' / '8 PM' / '20.30 Uhr' -> 'HH:MM'; None when absent."""
    raw = (value or "").replace(" ", " ").strip()
    if not raw:
        return None
    m = _TIME_RE.search(raw)
    if not m or (m.group(2) is None and m.group(3) is None):
        return None
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    suffix = (m.group(3) or "").lower().replace(".", "").replace(" ", "")
    if suffix == "pm" and hour < 12:
        hour += 12
    elif suffix == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def calendar_schedule(link):
    """The Google Calendar template link of an event -> its schedule:
    {start_date, start_time, end_date, end_time, timezone, start_time_utc,
    end_time_utc}. `dates=` holds local times in `ctz`; an event whose start
    and end are both midnight of the same day has no time (Google lists it
    as '12:00 AM')."""
    out = empty_schedule()
    params = parse_qs(urlparse(link or "").query)
    dates = (params.get("dates") or [""])[0].split("/")
    tz_name = (params.get("ctz") or [None])[0]
    out["timezone"] = tz_name
    parsed = []
    for value in dates[:2]:
        m = _CAL_DATE_RE.match(value.strip())
        if not m:
            parsed.append(None)
            continue
        y, mo, d, hh, mm, ss, z = m.groups()
        parsed.append((datetime(int(y), int(mo), int(d), int(hh or 0), int(mm or 0), int(ss or 0)), hh is not None, bool(z)))
    start = parsed[0] if parsed else None
    end = parsed[1] if len(parsed) > 1 else None
    untimed = start and end and start[0] == end[0] and start[0].hour == start[0].minute == 0
    zone = None
    if tz_name and ZoneInfo:
        try:
            zone = ZoneInfo(tz_name)
        except Exception:
            zone = None

    def fill(prefix, item, timed):
        if not item:
            return
        moment, has_time, is_utc = item
        out[f"{prefix}_date"] = moment.date().isoformat()
        if not (has_time and timed):
            return
        out[f"{prefix}_time"] = moment.strftime("%H:%M")
        if is_utc:
            utc = moment.replace(tzinfo=timezone.utc)
        elif zone is not None:
            utc = moment.replace(tzinfo=zone).astimezone(timezone.utc)
        else:
            return
        out[f"{prefix}_time_utc"] = utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    fill("start", start, not untimed)
    if not untimed and end and end[0] != start[0]:
        fill("end", end, True)
    return out


def empty_schedule():
    return {"start_date": None, "start_time": None, "end_date": None, "end_time": None, "timezone": None,
            "start_time_utc": None, "end_time_utc": None}


def cid_of(feature_id):
    """'0x8644b538bc720c67:0x582b04b3a6359876' -> '6353176868970403958' (the
    place's customer id, the ludocid / ?cid= of Google Maps)."""
    try:
        return str(int(str(feature_id).split(":")[1], 16))
    except (IndexError, ValueError):
        return None


def maps_link(cid):
    return f"https://www.google.com/maps?cid={cid}" if cid else None


def event_link(event_id, query, country="US", language="en"):
    """A Google link to an event: the results page it was listed on (the
    search whose carousel held it). The MID rides in the fragment, which
    Google ignores and /google-events/details reads — with it, details can
    re-find the event there when its token is no longer memoised. (A search
    for the event's own title rarely shows a carousel: 0 of 3, 2026-09-26.)"""
    if not query:
        return None
    link = f"{SITE}/search?" + urlencode({"q": query, "hl": language, "gl": (country or "us").lower()}, quote_via=quote_plus)
    return link + (f"#htidocid={event_id}" if event_id else "")


def domain_of(link):
    host = urlparse(link or "").hostname or ""
    return host[4:] if host.startswith("www.") else (host or None)


def _icon(el):
    path = first(el, "svg path")
    return (path.get("d") or "")[:30] if path is not None else ""


def is_event_id(value):
    return bool(_MID_RE.match(value or ""))


# ---- results page ------------------------------------------------------------------------------------------

def _card_rows(card):
    """(type, date text, venue, locality) of a card, rows told apart by icon."""
    rows = card.cssselect(".qXy4ec")
    if not rows:     # layout fallback: leaf spans after the heading
        heading = first(card, "[role='heading']")
        block = heading.getparent().getnext() if heading is not None and heading.getparent() is not None else None
        rows = [s for s in (block.iter("span") if block is not None else []) if not s.cssselect("span, svg")]
    kinds = []
    for row in rows:
        icon = _icon(row)
        kind = "date" if icon.startswith(ICON_DATE[:20]) else "venue" if icon.startswith(ICON_PLACE[:20]) else None
        kinds.append((kind, text(row)))
    date_at = next((i for i, (k, _) in enumerate(kinds) if k == "date"), None)
    venue_at = next((i for i, (k, _) in enumerate(kinds) if k == "venue"), None)
    anchor = date_at if date_at is not None else venue_at
    plain_before = [v for i, (k, v) in enumerate(kinds) if k is None and (anchor is None or i < anchor)]
    plain_after = [v for i, (k, v) in enumerate(kinds) if k is None and anchor is not None and i > anchor]
    return (
        plain_before[0] if plain_before and anchor is not None else None,
        kinds[date_at][1] if date_at is not None else None,
        kinds[venue_at][1] if venue_at is not None else None,
        plain_after[-1] if plain_after else (plain_before[-1] if anchor is None and plain_before else None),
    )


def page_query(doc):
    el = first(doc, "[data-async-context^='query:']")
    m = re.match(r"query:(.*)", (el.get("data-async-context") if el is not None else "") or "")
    if not m:
        return None
    from urllib.parse import unquote
    return unquote(m.group(1)) or None


def parse_results(value, *, country="US", language="en", now=None):
    """A results page -> {query, events, tokens}. `tokens` maps event id ->
    the viewer token of its card."""
    doc = markup.inflated_document(value)
    query = page_query(doc)
    lazy = markup.lazy_image_links(value)
    inline = deferred_images(value)
    events, tokens, seen = [], {}, set()
    for card in doc.cssselect("[data-ssid='event_viewer_entrypoint']"):
        mid_el = first(card, "[data-mid]")
        event_id = card.get("id") if is_event_id(card.get("id")) else (mid_el.get("data-mid") if mid_el is not None else None)
        title = text(first(card, "[role='heading']"))
        if not title or (event_id and event_id in seen):
            continue
        seen.add(event_id)
        type_, when, venue, locality = _card_rows(card)
        start_date, start_time = parse_card_datetime(when, now)
        img = first(card, "img")
        thumbnail = lazy.get(img.get("id")) if img is not None else None
        schedule = empty_schedule()
        schedule.update({"start_date": start_date, "start_time": start_time})
        events.append({
            "position": len(events) + 1,
            "id": event_id,
            "title": title,
            "link": event_link(event_id, query, country, language),
            "type": type_,
            "schedule": schedule,
            "venue": {"name": venue, "locality": locality},
            "thumbnail": thumbnail or image_src(img, inline),
        })
        token = card.get("data-async-fc")
        if event_id and token:
            tokens[event_id] = token
    return {"query": query, "events": events, "tokens": tokens}


# ---- viewer (event details) ----------------------------------------------------------------------------------

def _viewer_js(value):
    """What the viewer's JS data holds: event MID + title, venue feature id,
    images, venue record, venue cid, venue description."""
    out = {"event_id": None, "title": None, "feature_id": None, "images": [], "venue": None, "cid": None,
           "venue_description": None, "photo": None}
    for data in markup.js_values(value):
        for node in markup.walk(data):
            if isinstance(node, dict):
                if "193" in node and not out["feature_id"]:
                    row = node["193"][0] if isinstance(node["193"], list) and node["193"] else None
                    if isinstance(row, list) and len(row) > 1:
                        out["feature_id"] = row[0] if isinstance(row[0], str) and ":" in row[0] else None
                        out["event_id"] = row[1] if is_event_id(row[1]) else out["event_id"]
                if "194" in node and not out["title"]:
                    row = node["194"][0] if isinstance(node["194"], list) and node["194"] else None
                    if isinstance(row, list) and len(row) > 2 and isinstance(row[1], str):
                        out["title"] = row[1]
                        out["event_id"] = out["event_id"] or (row[2] if is_event_id(row[2]) else None)
                if "498469725" in node and out["venue"] is None and isinstance(node["498469725"], list):
                    out["venue"] = node["498469725"]
            elif isinstance(node, list):
                # [[{"193"…}], title, null, [[image, null, null, page, domain], [map]], …]
                if (len(node) > 3 and isinstance(node[0], list) and node[0] and isinstance(node[0][0], dict)
                        and "193" in node[0][0] and isinstance(node[3], list) and not out["images"]):
                    out["images"] = node[3]
                # venue description block: [null, [["<text>"], 1, 2, [...]], …]
                if (not out["venue_description"] and len(node) > 1 and node[0] is None and isinstance(node[1], list)
                        and node[1] and isinstance(node[1][0], list) and node[1][0] and isinstance(node[1][0][0], str)
                        and len(node[1][0][0]) > 20 and len(node[1]) > 2 and node[1][1] == 1):
                    out["venue_description"] = node[1][0][0]
            elif isinstance(node, str):
                if not out["cid"]:
                    m = re.search(r"[?&]ludocid=(\d+)", node)
                    if m:
                        out["cid"] = m.group(1)
                if not out["photo"] and node.startswith("https://lh3.googleusercontent.com/"):
                    out["photo"] = node
    return out


def _images(raw):
    images, map_image = [], None
    for item in raw if isinstance(raw, list) else []:
        if not (isinstance(item, list) and item and isinstance(item[0], str)):
            continue
        link = item[0]
        link = "https:" + link if link.startswith("//") else link
        if "/maps/vt/" in link:
            map_image = map_image or link
            continue
        page = item[3] if len(item) > 3 and isinstance(item[3], str) else None
        images.append({"link": link, "source_link": page,
                       "source_domain": (item[4] if len(item) > 4 and isinstance(item[4], str) else None) or domain_of(page)})
    return images, map_image


def _venue_record(raw):
    """The 498469725 venue record -> (name, rating, review count, type, mid)."""
    if not isinstance(raw, list):
        return None, None, None, None, None
    name = raw[0] if raw and isinstance(raw[0], str) else None
    stats = raw[1] if len(raw) > 1 and isinstance(raw[1], list) else []
    rating = round(stats[0], 1) if stats and isinstance(stats[0], (int, float)) else None
    reviews = None
    if len(stats) > 3 and isinstance(stats[3], list) and stats[3]:
        reviews = to_int(stats[3][0])
    kinds = raw[3] if len(raw) > 3 and isinstance(raw[3], list) else []
    kind = None
    for entry in kinds:
        if isinstance(entry, list) and len(entry) > 1 and isinstance(entry[1], list) and entry[1] \
                and isinstance(entry[1][0], str) and len(entry[1]) == 1:
            kind = entry[1][0]
            break
    ids = raw[9] if len(raw) > 9 and isinstance(raw[9], list) else []
    mid = ids[0] if ids and is_event_id(ids[0]) else None
    return name, rating, reviews, kind, mid


def _actions(doc):
    """(website, calendar link, phone) from the viewer's action bar."""
    website = calendar = phone = None
    for a in doc.cssselect("a[href]"):
        href = a.get("href") or ""
        if href.startswith("tel:") and not phone:
            phone = href[4:].strip() or None
        elif "calendar.google.com/calendar" in href and not calendar:
            calendar = href
        elif "lwcikd" in (a.get("class") or "") and href.startswith("http") and not website \
                and "google." not in (urlparse(href).hostname or ""):
            website = href
    return website, calendar, phone


def _street_address(doc):
    for a in doc.cssselect("a[href*='daddr=']"):
        daddr = (parse_qs(urlparse(a.get("href")).query).get("daddr") or [None])[0]
        if daddr:
            return daddr
    return None


def _ticket_links(doc, country):
    out = []
    for a in doc.cssselect("a.Zxvcad[href], a[gl-helper-surface='ttd-events'][href]"):
        name = text(first(a, ".W0LCVe"))
        price_el = first(a, ".Nten9d")
        if price_el is None:
            price_el = first(a, ".gSMDJ [class*='n6c45b']")
        amount, currency = money(text(price_el), country) if price_el is not None else (None, None)
        href = a.get("href")
        out.append({
            "source": name,
            "link": ("https://www.google.com" + href) if href.startswith("/") else href,
            "is_official_seller": first(a, ".Pt08Vc") is not None or first(a, ".trIm2e") is not None,
            "min_price": amount,
            "currency": currency if amount is not None else None,
        })
    return out


def _description(doc):
    block = first(doc, ".rUh2g")
    if block is None:
        return None, None
    source = first(block, "a.Vovx8b")
    if source is None:
        source = first(block.getparent(), "a.Vovx8b")
    link = source.get("href") if source is not None else None
    if source is not None and source.getparent() is not None:
        tail = source.tail
        source.getparent().remove(source)
        if tail:
            block.text = (block.text or "") + tail
    return clean_text(block.text_content()), link


def parse_details(value, *, country="US", language="en"):
    """A viewer chunk -> the event object, or None when it holds no event."""
    doc = markup.chunk_document(value)
    js = _viewer_js(value)
    title = js["title"] or text(first(doc, "h3"))
    if not title:
        return None
    website, calendar, phone = _actions(doc)
    schedule = calendar_schedule(calendar)
    if not schedule["start_date"]:
        line = [text(s) for s in doc.cssselect("a.fuxI span")]
        start_date, start_time = parse_card_datetime(" ".join(x for x in line if x), None)
        schedule.update({"start_date": start_date, "start_time": start_time})
    images, map_image = _images(js["images"])
    v_name, v_rating, v_reviews, v_type, v_mid = _venue_record(js["venue"])
    full_address = text(first(doc, ".C1cKff"))
    street = _street_address(doc)
    venue_name = v_name or (full_address.split(",")[0].strip() if full_address and "," in full_address else None)
    if not street and full_address and venue_name and full_address.startswith(venue_name + ","):
        street = full_address[len(venue_name) + 1:].strip()
    cid = js["cid"] or cid_of(js["feature_id"])
    description, description_source = _description(doc)
    source_line = text(first(doc, ".A6mSHd"))
    source_domain = (images[0]["source_domain"] if images else None) or \
        (source_line.split(":", 1)[1].strip() if source_line and ":" in source_line else None)
    venue_description = js["venue_description"] or text(first(doc, ".A5yTVb"))
    return {
        "id": js["event_id"],
        "title": clean_text(title),
        "link": None,
        "type": None,
        "description": description,
        "schedule": schedule,
        "venue": {
            "id": v_mid,
            "name": venue_name,
            "type": v_type,
            "rating": v_rating,
            "review_count": v_reviews,
            "address": street or full_address,
            "locality": None,
            "phone": phone,
            "website": website,
            "description": venue_description,
            "photo": js["photo"],
            "cid": cid,
            "feature_id": js["feature_id"],
            "google_maps_link": maps_link(cid),
        },
        "ticket_and_info_links": _ticket_links(doc, country),
        "images": images,
        "thumbnail": images[0]["link"] if images else None,
        "map_image": map_image,
        "source": {"domain": source_domain, "link": images[0]["source_link"] if images else None,
                   "description_link": description_source},
        "google_calendar_link": calendar,
    }
