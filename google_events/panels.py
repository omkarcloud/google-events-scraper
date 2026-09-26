"""The "<entity> / Events" panel of a results page (verified 2026-09-26):
Google's list of a venue's upcoming events ("madison square garden upcoming
events" -> "Madison Square Garden / Events", 20 events) or an artist's dates
near a place ("phoebe bridgers new york" -> "Phoebe Bridgers / Events",
"Near New York").

  header  [data-attrid="title"] with the "Events" crumb .ESb20e; the entity
          name is its heading link (a Google search for the entity)
  near    .HPyNyb "Near <place>" (artist panels)
  items   [role=listitem] > .dnXCYb (the collapsed row) + a.Zxvcad ticket
          sellers (the expanded row, always in the HTML)
          venue row   img · .Sw5kW title · .YmZCTd "Sat, Sep 26, 8:00 AM" ·
                      "from $79" .Nten9d
          artist row  .gW1qLc "Sep" + .YBjDnb "26" · .Sw5kW city · .YmZCTd
                      "Sat 7:00 PM · Barclays Center" · .Nten9d price
Panel rows carry no event id or viewer token.
"""
from google_events import markup
from google_events.parsers import _ticket_links, empty_schedule, parse_card_datetime
from google_search.serp_parsers import deferred_images, first, image_src, text
from google_shopping.parsers import money


def _header(doc):
    for el in doc.cssselect("[data-attrid='title']"):
        if first(el, ".ESb20e") is not None:
            return el
    return None


def _container(header):
    node = header
    for _ in range(12):
        node = node.getparent()
        if node is None:
            return None
        if node.cssselect("[role='listitem'] .dnXCYb"):
            return node
    return None


def parse_panel(value, *, country="US", now=None):
    """A results page -> {name, near, events} of its events panel, or
    None when the page has none."""
    doc = markup.inflated_document(value)
    header = _header(doc)
    box = _container(header) if header is not None else None
    if box is None:
        return None
    heading = first(header, "[role='heading']")
    name = text(heading) or text(first(header, "a[href]"))
    near_el = first(box, ".HPyNyb")
    near = text(near_el)
    near_place = text(first(near_el, "span span span")) if near_el is not None else None
    inline = deferred_images(value)
    lazy = markup.lazy_image_links(value)
    events = []
    for item in box.cssselect("[role='listitem']"):
        row = first(item, ".dnXCYb")
        if row is None:
            continue
        label = text(first(row, ".Sw5kW"))
        line = text(first(row, ".YmZCTd"))
        month, day = text(first(row, ".gW1qLc")), text(first(row, ".YBjDnb"))
        venue_name = locality = None
        if month and day:        # artist row: the label is the city, the line "Sat 7:00 PM · Venue"
            when_text, _, venue_name = (line or "").partition("·")
            start_date, start_time = parse_card_datetime(f"{month} {day}, {when_text}", now)
            title, locality, venue_name = name, label, venue_name.strip() or None
        else:                    # venue row: the label is the event, the line its date
            start_date, start_time = parse_card_datetime(line, now)
            title, venue_name = label, name
        price_el = first(row, ".Nten9d")
        amount, currency = money(text(price_el), country) if price_el is not None else (None, None)
        img = first(row, "img")
        schedule = empty_schedule()
        schedule.update({"start_date": start_date, "start_time": start_time})
        events.append({
            "position": len(events) + 1,
            "title": title,
            "schedule": schedule,
            "venue": {"name": venue_name, "locality": locality},
            "min_price": amount,
            "currency": currency if amount is not None else None,
            "thumbnail": (lazy.get(img.get("id")) if img is not None else None) or image_src(img, inline),
            "ticket_and_info_links": _ticket_links(item, country),
        })
    if not events:
        return None
    return {"name": name, "near": near_place or (near.split(" ", 1)[1] if near and " " in near else near),
            "events": events}
