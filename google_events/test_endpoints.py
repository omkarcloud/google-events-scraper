"""Live endpoint smoke tests: one call per /google-events/* route against a
running service, with example values proven to return data (2026-09-26).
The listing tooling reads each route's FIRST call from this file's AST as
its working example, so the values stay literals.

Skipped unless GOOGLE_EVENTS_BASE points at a running service:

    SCRAPERS_PORT=6062 ONLY_SCRAPER=google-events python run.py
    GOOGLE_EVENTS_BASE=http://127.0.0.1:6062 python -m pytest google_events/test_endpoints.py -q

Results pages and the event viewer run on minted SERP identities (a headed
Camoufox + the CapSolver extension, 30-240 s for the first one), so the
first call is slow.
"""
import os

import pytest

BASE = os.environ.get("GOOGLE_EVENTS_BASE", "").rstrip("/")

pytestmark = pytest.mark.skipif(not BASE, reason="set GOOGLE_EVENTS_BASE to run live endpoint tests")


def call(path, **params):
    from curl_cffi import requests
    resp = requests.get(BASE + path, params=params, timeout=600)
    assert resp.status_code == 200, f"{path} {params} -> {resp.status_code} {resp.text[:300]}"
    body = resp.json()
    assert body, f"{path} returned an empty body"
    return body


def status(path, **params):
    from curl_cffi import requests
    return requests.get(BASE + path, params=params, timeout=600).status_code


def test_search():
    body = call("/google-events/search", query="concerts", location="New York", date="this_weekend")
    events = body["events"]
    assert body["count"] >= 5 and len(events) == body["count"]
    event = events[0]
    assert event["id"].startswith("/g/") and event["title"] and event["schedule"]["start_date"]
    assert event["venue"]["name"] and event["link"].startswith("https://www.google.com/search?")
    assert body["search_information"]["query"] == "concerts in New York this weekend"


def test_search_with_details():
    body = call("/google-events/search", query="comedy shows in chicago", include_details="true")
    event = body["events"][0]
    assert event["schedule"]["timezone"] == "America/Chicago" and event["venue"]["address"]
    assert event["ticket_and_info_links"] and event["ticket_and_info_links"][0]["link"].startswith("http")
    assert "/goto?" not in event["ticket_and_info_links"][0]["link"]


def test_details():
    found = call("/google-events/search", query="events in austin")["events"]
    body = call("/google-events/details", event=found[0]["id"])
    assert body["id"] == found[0]["id"] and body["title"] and body["schedule"]["start_date"]
    assert body["venue"]["name"] and body["venue"]["cid"] and body["google_calendar_link"]
    by_link = call("/google-events/details", event=found[0]["link"])
    assert by_link["id"] == found[0]["id"]


def test_details_batch():
    found = call("/google-events/search", query="events in london", country="GB")["events"]
    ids = ",".join(e["id"] for e in found[:3])
    body = call("/google-events/details/batch", events=ids, country="GB")
    assert len(body["events"]) + len(body["not_found"]) == 3 and body["events"]
    assert body["events"][0]["schedule"]["timezone"] == "Europe/London"


def test_venue_events():
    body = call("/google-events/venue-events", venue="Madison Square Garden")
    assert body["venue"]["name"] == "Madison Square Garden" and body["count"] >= 5
    event = body["events"][0]
    assert event["title"] and event["schedule"]["start_date"] and event["ticket_and_info_links"]


def test_artist_events():
    body = call("/google-events/artist-events", artist="Phoebe Bridgers")
    assert body["artist"]["name"] == "Phoebe Bridgers" and body["count"] >= 2
    event = body["events"][0]
    assert event["venue"]["name"] and event["venue"]["locality"] and event["schedule"]["start_date"]
    near = call("/google-events/artist-events", artist="Phoebe Bridgers", location="New York")
    assert near["near"] and near["count"] >= 1


def test_autocomplete():
    body = call("/google-events/autocomplete", query="concerts in aus")
    assert body["count"] >= 3 and all(s["text"] for s in body["suggestions"])


def test_bad_requests():
    assert status("/google-events/search") == 400
    assert status("/google-events/search", query="concerts", date="yesterday") == 400
    assert status("/google-events/details", event="hello world") == 400
    assert status("/google-events/details", event="/g/11zzzzzzzz") == 404
