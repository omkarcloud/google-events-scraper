"""Offline tests for google_events: markup, parsers, panels, references,
schemas and the query builder against captured responses
(google_events/fixtures/*.gz, 2026-09-24/26):

  serp.html                "events in austin" (hl=en, gl=us): 10-card carousel,
                           4 inline + 6 deferred jsl.dh fragments
  serp_gb.html             "events in london" (gl=gb): 24-hour times, "26 Sept"
  serp_ca.html             "events in toronto" (gl=ca): "4:00 p.m."
  serp_no_events.html      "online events": no carousel
  events_page_retired.html ibp=htl;events: Google's retired Events page (empty shell)
  detail.txt               viewer of Phoebe Bridgers at Barclays Center (15 sellers, a price)
  detail_official_price.txt viewer with an "Official seller" badge + "from $30"
  detail_gb.txt            a London viewer (Europe/London, relative date)
  detail_untimed.txt       a viewer whose calendar link is midnight-to-midnight
  detail_old_token.txt     the viewer of a 2-day-old token replayed by another identity
  stub.txt                 the refusal stub a callback answers without identity cookies
  venue_panel.html         "madison square garden upcoming events": 20-event venue panel
  artist_panel.html        "phoebe bridgers tickets": the tour (20 dates)
  artist_panel_near.html   "Phoebe Bridgers New York, NY": dates near New York

    cd scrapers && python -m pytest google_events/test_parsers.py -q
"""
import base64
import gzip
import json
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google_events import events as E, markup, panels, parsers as P, refs  # noqa: E402

FX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def load(name):
    with gzip.open(os.path.join(FX, name + ".gz"), "rt", encoding="utf-8", errors="replace") as f:
        return f.read()


# ---- markup ------------------------------------------------------------------------------------------------

def test_deferred_fragments_are_inflated_in_order():
    page = load("serp.html")
    assert len(markup.deferred_fragments(page)) >= 6
    doc = markup.inflated_document(page)
    ids = [c.get("id") for c in doc.cssselect("[data-ssid='event_viewer_entrypoint']")]
    assert len(ids) == 10 and ids[:2] == ["/g/11zxmtbr7v", "/g/11zf7d8nkg"]


def test_refusal_stub_and_served_chunk():
    assert markup.is_refused(load("stub.txt"))
    assert not markup.is_refused(load("detail.txt"))
    assert "Tickets" in markup.chunk_html(load("detail.txt"))


# ---- values ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("Sep 30, 7:00 PM", ("2026-09-30", "19:00")),
    ("26 Sept, 19:30", ("2026-09-26", "19:30")),
    ("Oct 1, 4:00 p.m.", ("2026-10-01", "16:00")),
    ("8 Oct, 3:30 pm", ("2026-10-08", "15:30")),
    ("Sat, Sep 26, 8:00 AM", ("2026-09-26", "08:00")),
    ("Sep 28", ("2026-09-28", None)),
    ("Jan 4, 9:00 PM", ("2027-01-04", "21:00")),           # past month -> next year
    ("Wed, Sep 30, 2026, 12:00 AM", ("2026-09-30", "00:00")),
    ("12. Okt., 20:00", ("2026-10-12", "20:00")),
    ("Thu, Aug 6, 7:00 PM", ("2026-08-06", "19:00")),     # the weekday picks the year: a past event
    ("Fri, Aug 6, 7:00 PM", ("2027-08-06", "19:00")),
    ("Sep 26 Sat 7:00 PM", ("2026-09-26", "19:00")),      # artist-panel row
    ("Tomorrow", (None, None)),
    (None, (None, None)),
])
def test_parse_card_datetime(value, expected):
    assert P.parse_card_datetime(value, NOW) == expected


def test_calendar_schedule_converts_to_utc():
    s = P.calendar_schedule("https://calendar.google.com/calendar/render?action=TEMPLATE&text=X&"
                            "dates=20260926T190000/20260926T210000&ctz=America/New_York")
    assert s == {"start_date": "2026-09-26", "start_time": "19:00", "end_date": "2026-09-26", "end_time": "21:00",
                 "timezone": "America/New_York", "start_time_utc": "2026-09-26T23:00:00Z",
                 "end_time_utc": "2026-09-27T01:00:00Z"}
    untimed = P.calendar_schedule("https://calendar.google.com/calendar/render?dates=20260930T000000/20260930T000000"
                                  "&ctz=America/Chicago")
    assert untimed["start_date"] == "2026-09-30" and untimed["start_time"] is None and untimed["end_date"] is None
    assert P.calendar_schedule(None) == P.empty_schedule()


def test_cid_of_feature_id():
    assert P.cid_of("0x8644b538bc720c67:0x582b04b3a6359876") == "6353176868970403958"
    assert P.cid_of("nonsense") is None


# ---- results page -------------------------------------------------------------------------------------------

def test_parse_results_carousel():
    out = P.parse_results(load("serp.html"), now=NOW)
    assert out["query"] == "events in austin"
    events = out["events"]
    assert len(events) == 10 and len(out["tokens"]) == 10
    jay = events[1]
    assert jay["id"] == "/g/11zf7d8nkg" and jay["title"] == "Jay Hooks" and jay["type"] == "Texas blues concert"
    assert jay["schedule"]["start_date"] == "2026-09-26" and jay["schedule"]["start_time"] == "20:00"
    assert jay["venue"] == {"name": "Moontower Saloon", "locality": "Austin, TX"}
    assert jay["link"] == "https://www.google.com/search?q=events+in+austin&hl=en&gl=us#htidocid=/g/11zf7d8nkg"
    assert events[0]["type"] is None          # the type row is optional
    assert all(e["thumbnail"] for e in events)
    assert events[-1]["thumbnail"].startswith("https://encrypted-tbn")   # deferred cards: google.ldi URLs
    assert events[0]["thumbnail"].startswith("data:image/")              # inline cards: data URIs
    assert [e["position"] for e in events] == list(range(1, 11))


def test_parse_results_localized_times():
    gb = P.parse_results(load("serp_gb.html"), country="GB", now=NOW)["events"]
    assert gb[1]["title"] == "Bury Me Before You Marry Me"
    assert gb[1]["schedule"]["start_date"] == "2026-09-28" and gb[1]["schedule"]["start_time"] == "19:30"
    assert gb[1]["venue"]["locality"] == "London, United Kingdom"
    ca = P.parse_results(load("serp_ca.html"), country="CA", now=NOW)["events"]
    assert len(ca) == 10 and ca[0]["schedule"]["start_time"] == "16:00"


def test_no_carousel_and_retired_page():
    assert P.parse_results(load("serp_no_events.html"), now=NOW)["events"] == []
    assert P.parse_results(load("events_page_retired.html"), now=NOW)["events"] == []


# ---- viewer -------------------------------------------------------------------------------------------------

def test_parse_details():
    d = P.parse_details(load("detail.txt"))
    assert d["id"] == "/g/11nptpqngy" and d["title"] == "Phoebe Bridgers"
    assert d["description"].startswith("Buy Phoebe Bridgers tickets at Barclays Center")
    assert d["schedule"] == {"start_date": "2026-09-26", "start_time": "19:00", "end_date": "2026-09-26",
                             "end_time": "21:00", "timezone": "America/New_York",
                             "start_time_utc": "2026-09-26T23:00:00Z", "end_time_utc": "2026-09-27T01:00:00Z"}
    v = d["venue"]
    assert v["id"] == "/m/05l9fg" and v["name"] == "Barclays Center" and v["type"] == "Arena"
    assert v["rating"] == 4.5 and v["review_count"] == 23830
    assert v["address"] == "620 Atlantic Ave, Brooklyn, NY 11217" and v["phone"] == "+19176186100"
    assert v["website"] == "http://www.barclayscenter.com/" and v["description"].startswith("Home to the Brooklyn Nets")
    assert v["cid"] == "13372475396672644766" == P.cid_of(v["feature_id"])
    assert v["google_maps_link"] == "https://www.google.com/maps?cid=13372475396672644766"
    assert v["photo"].startswith("https://lh3.googleusercontent.com/")
    sellers = d["ticket_and_info_links"]
    assert len(sellers) == 15 and sellers[0]["source"] == "Ticketmaster" and sellers[0]["is_official_seller"]
    assert sellers[0]["min_price"] == 168.0 and sellers[0]["currency"] == "USD"
    assert sellers[1]["min_price"] is None and sellers[1]["currency"] is None
    assert d["images"][0]["source_domain"] == "ticketmaster.com" and d["source"]["domain"] == "ticketmaster.com"
    assert d["map_image"].startswith("https://www.google.com/maps/vt/")
    assert d["google_calendar_link"].startswith("https://calendar.google.com/calendar/render")


def test_parse_details_official_seller_price_and_description_source():
    d = P.parse_details(load("detail_official_price.txt"))
    seller = d["ticket_and_info_links"][0]
    assert seller["source"] == "Eventbrite" and seller["is_official_seller"] and seller["min_price"] == 30.0
    assert d["venue"]["name"] == "Laugh Factory" and d["venue"]["type"] == "Comedy club"
    assert not d["description"].endswith("›")


def test_parse_details_gb_and_description_source_link():
    d = P.parse_details(load("detail_gb.txt"), country="GB")
    assert d["schedule"]["timezone"] == "Europe/London" and d["schedule"]["start_time"] == "19:30"
    assert d["schedule"]["start_time_utc"] == "2026-09-28T18:30:00Z"       # BST
    assert d["source"]["description_link"] == "https://eventsforlondon.co.uk/event/bury_me_before_you_marry_me/2026-09-28/"
    assert "Source" not in d["description"]


def test_parse_details_untimed_and_old_token():
    d = P.parse_details(load("detail_untimed.txt"))
    assert d["title"] == "Young Thug" and d["schedule"]["start_date"] == "2026-09-30"
    assert d["schedule"]["start_time"] is None and d["schedule"]["timezone"] == "America/Chicago"
    assert d["venue"]["cid"] == "6353176868970403958"
    assert P.parse_details(load("detail_old_token.txt"))["title"]
    assert P.parse_details(load("stub.txt")) is None


# ---- panels -------------------------------------------------------------------------------------------------

def test_venue_panel():
    p = panels.parse_panel(load("venue_panel.html"), now=NOW)
    assert p["name"] == "Madison Square Garden" and p["near"] is None and len(p["events"]) == 20
    first = p["events"][0]
    assert first["title"] == "Harry Styles" and first["venue"]["name"] == "Madison Square Garden"
    assert first["schedule"]["start_date"] == "2026-09-26" and first["schedule"]["start_time"] == "20:00"
    assert len(first["ticket_and_info_links"]) == 10
    priced = p["events"][1]
    assert priced["min_price"] == 140.0 and priced["currency"] == "USD"


def test_artist_panels():
    tour = panels.parse_panel(load("artist_panel.html"), now=NOW)
    assert tour["name"] == "Phoebe Bridgers" and len(tour["events"]) == 20
    first = tour["events"][0]
    assert first["title"] == "Phoebe Bridgers" and first["venue"] == {"name": "Barclays Center", "locality": "New York, NY"}
    assert first["schedule"]["start_date"] == "2026-09-26" and first["schedule"]["start_time"] == "19:00"
    near = panels.parse_panel(load("artist_panel_near.html"), now=NOW)
    assert near["near"] == "New York" and len(near["events"]) == 2
    assert near["events"][1]["venue"]["name"] == "Xfinity Mobile Arena"
    assert panels.parse_panel(load("serp.html"), now=NOW) is None


# ---- references ---------------------------------------------------------------------------------------------

def test_resolve_event_forms():
    assert refs.resolve_event("/g/11zf7d8nkg") == {"id": "/g/11zf7d8nkg", "token": None, "query": None}
    assert refs.resolve_event("%2Fg%2F11zf7d8nkg")["id"] == "/g/11zf7d8nkg"
    link = "https://www.google.com/search?q=Jay+Hooks+Austin%2C+TX&hl=en&gl=us#htidocid=/g/11zf7d8nkg"
    assert refs.resolve_event(link) == {"id": "/g/11zf7d8nkg", "token": None, "query": "Jay Hooks Austin, TX"}
    blob = base64.b64encode(json.dumps({"fc": "EswHCowHQUpp", "e": "/g/11zf7d8nkg"}).encode()).decode()
    assert refs.resolve_event(blob) == {"id": "/g/11zf7d8nkg", "token": "EswHCowHQUpp", "query": None}
    token = "EswHCowHQUppVDR0SVR6VEpuSUdiNU1nbzM4TEFuaWpfUnY4WVhGaElMWi1UcGVfUkZ2TE5pWDB0cnliaGM3LUFQTGlVSG9HZDAz" \
            "T2tQSjhxbEQ5Yy1kMmFqZThuSDMtX1B5cF9nRE16aXplQnRIemkwN2wzYzhsY1lC"
    assert refs.resolve_event(token) == {"id": None, "token": token, "query": None}
    assert refs.resolve_event(f"https://maps.google.com/maps?fc={token}&daddr=x")["token"] == token


@pytest.mark.parametrize("value,message", [
    ("", "must not be empty"),
    ("hello world", "event id"),
    ("https://example.com/event", "event id"),
    ("https://www.google.com/search?q=events", "must carry the event"),
    ("L2F1dGhvcml0eS9ob3Jpem9uL2NsdXN0ZXJlZF9ldmVudC8yMDI0LTA1LTI1fF8xNTAyNDE4NTg5MTkzOTA1NTMzOA==", "retired"),
])
def test_resolve_event_errors(value, message):
    with pytest.raises(ValueError, match=message):
        refs.resolve_event(value)


def test_resolve_name():
    assert refs.resolve_name("  Madison   Square Garden ", "venue") == "Madison Square Garden"
    assert refs.resolve_name("https://www.google.com/search?q=Moody+Center", "venue") == "Moody Center"
    with pytest.raises(ValueError):
        refs.resolve_name("https://example.com", "venue")


# ---- query + schemas ------------------------------------------------------------------------------------------

def test_build_query():
    assert E.build_query("concerts", "Austin, TX", "this_weekend") == "concerts in Austin, TX this weekend"
    assert E.build_query("events in austin", "Austin", "any") == "events in austin"
    assert E.build_query("comedy shows", None, "today", online_only=True) == "online comedy shows today"
    assert E.build_query("events this weekend", None, "this_weekend") == "events this weekend"


def test_schemas():
    from google_events import schemas as S
    data = S.SearchSchema().load({"query": "concerts", "date": "weekend", "include_details": "true"})
    assert data["date"] == "this_weekend" and data["include_details"] is True and data["country"] == "US"
    data = S.SearchSchema().load({"query": "https://www.google.com/search?q=events+in+london&gl=uk&hl=en-GB"})
    assert data["query"] == "events in london" and data["country"] == "GB" and data["language"] == "en"
    from marshmallow import ValidationError
    with pytest.raises(ValidationError):
        S.SearchSchema().load({"query": "x", "date": "yesterday"})
    batch = S.EventBatchSchema().load({"events": "/g/11zf7d8nkg, /g/11zf7d8nkg,/g/11nptpqngy"})
    assert [e["id"] for e in batch["events"]] == ["/g/11zf7d8nkg", "/g/11nptpqngy"]
    with pytest.raises(ValidationError):
        S.EventBatchSchema().load({"events": ",".join(f"/g/11abc{i:04d}" for i in range(11))})
    assert S.ArtistEventsSchema().load({"artist": "Phoebe Bridgers"})["location"] is None
    assert S.VenueEventsSchema().load({"venue": "Madison Square Garden", "location": "  "})["location"] is None
    with pytest.raises(ValidationError):
        S.SearchSchema().load({"query": "concerts", "location": "x" * 101})
