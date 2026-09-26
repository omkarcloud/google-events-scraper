"""Use the scraper straight from Python — no server needed.

    python main.py

Every function returns the same JSON the API does; results are written to
output/*.json. See README.md for every endpoint.
"""
import json
import os

from google_events.events import artist_events, details, search

os.makedirs("output", exist_ok=True)


def save(name, data):
    path = os.path.join("output", name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"saved {path}")


if __name__ == "__main__":
    # Google's events for a search: title, type, date, time, venue, thumbnail
    found = search("concerts in New York")
    save("search_concerts_in_new_york.json", found)

    # the full record of the first event: schedule in UTC, ticket sellers with prices, venue
    if found["events"]:
        save("event_details.json", details({"id": found["events"][0]["id"], "token": None, "query": None}))

    # an artist's tour: every date with its venue, starting price and ticket sellers
    save("artist_events_harry_styles.json", artist_events("Harry Styles"))
