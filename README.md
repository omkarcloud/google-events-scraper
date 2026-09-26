# 🎟️ Google Events Scraper

Google Events Scraper is a **free and open-source** scraper that gets you **unlimited** detailed Google Events data for free.

## ✨ What Can I Get?

- 🔍 **Every event Google shows, in one call** — concerts, comedy, festivals & more for any city and date
- 🎟️ **Every ticket seller with prices** — Ticketmaster, SeatGeek, StubHub & more, official seller flagged
- 🏟️ **Full schedule & venue details** — local and UTC times, address, rating, phone & website
- 🎤 **Artist tours & venue calendars** — up to 20 upcoming dates each, with starting prices

## 🎥 Example: A Full Google Event

```json
{
  "id": "/g/11yy1nc_pt",
  "title": "ANDREA BOCELLI \"Romanza - 30th Anniversary World Tour\"",
  "type": "Classical-pop crossover concert",
  "schedule": {
    "start_date": "2026-12-16",
    "start_time": "20:00",
    "end_time": "21:30",
    "timezone": "America/New_York",
    "start_time_utc": "2026-12-17T01:00:00Z"
  },
  "venue": {
    "name": "Madison Square Garden",
    "type": "Arena",
    "rating": 4.7,
    "review_count": 35160,
    "phone": "+12124656000",
    "website": "https://www.msg.com/madison-square-garden",
    "google_maps_link": "https://www.google.com/maps?cid=3737724789719234788"
  },
  "ticket_and_info_links": [
    { "source": "Ticketmaster", "domain": "ticketmaster.com", "is_official_seller": true, "min_price": 174.0, "currency": "USD" },
    { "source": "SeatGeek", "domain": "seatgeek.com", "is_official_seller": false, "min_price": 217.0, "currency": "USD" }
  ]
}
```

*Trimmed for readability.*

## 🚀 Unlimited Free Google Events Data — Get It in 60 Seconds

1️⃣ Clone and install (the last command downloads Camoufox, the hardened Firefox that opens Google for you):
```bash
git clone https://github.com/omkarcloud/google-events-scraper
cd google-events-scraper
python -m pip install -r requirements.txt
python -m camoufox fetch
```

2️⃣ Start the API:
```bash
python run.py
```

3️⃣ Get your first data:
```bash
curl "http://localhost:8000/search?query=concerts+in+New+York"
```

```json
{
  "search_information": { "query": "concerts in New York", "link": "https://www.google.com/search?q=concerts+in+New+York&hl=en&gl=us" },
  "count": 10,
  "events": [
    {
      "position": 1,
      "id": "/g/11zyssjqy9",
      "title": "Phoebe Bridgers",
      "schedule": { "start_date": "2026-09-26", "start_time": "19:30" },
      "venue": { "name": "Barclays Center", "locality": "Prospect Heights" },
      "thumbnail": "https://encrypted-tbn2.gstatic.com/images?q=tbn:ANd9GcQi1MIxWtbyAFvSg8cp9yRPock97nrWik8ra5F3PAhDvhbBSmm1DJCbSQV0lSmAeDAWYYgAjYxeqzSDzGM"
    },
    {
      "position": 3,
      "id": "/g/11zyss95rf",
      "title": "Elder",
      "schedule": { "start_date": "2026-10-02", "start_time": "19:30" },
      "venue": { "name": "Webster Hall", "locality": "East Village" }
    }
  ]
}
```

All 6 endpoints are now live at `http://localhost:8000`. Pass any event's `id` to `/details` for its full record.

The first search can take a few minutes while a browser opens Google and clears its checks; after that, each call takes a second or two.

### 🔓 Google Showing a Captcha?

Google often answers a fresh browser with an "unusual traffic" captcha. The scraper first tries on its own — no key, no cost — and when Google insists, it tells you so. Then:

1. Get a [CapSolver](https://dashboard.capsolver.com/passport/register?inviteCode=lvdYBC4sYKRm) API key (one captcha is solved per ~30 searches).
2. Start the API with it:
   ```bash
   CAPSOLVER_API_KEY=CAP-... python run.py
   ```

For heavy use, add a sticky residential proxy too — Google pauses one IP after a few dozen searches:

```bash
GOOGLE_EVENTS_PROXY="http://user-session-{session}:pass@host:port" CAPSOLVER_API_KEY=CAP-... python run.py
```

`{session}` gets a fresh id for every browser session. See [config.py](config.py) for every option.

Autocomplete needs none of this — it is plain HTTP and works straight away. Google's event pages work best when you run the scraper on macOS or Windows.

## 📚 Endpoints

6 endpoints cover everything you need.

| Endpoint | Path | Returns |
|---|---|---|
| Event Details | `/details` | UTC schedule, every ticket seller with prices, full venue |
| Autocomplete | `/autocomplete` | Google's event-search suggestions as you type |
| Search Events | `/search` | Up to 10 events for any city, date or genre |
| Batch Event Details | `/details/batch` | Full records for up to 10 events in one call |
| Artist Events | `/artist-events` | An artist's tour: up to 20 dates with prices |
| Venue Events | `/venue-events` | A venue's next 20 events with sellers and prices |

## 🔍 Exploring Parameters

The same API is published on RapidAPI, and its playground is the easiest place to try parameters and see raw responses. Once a request looks right, run it locally for **unlimited free** data.

1. [Subscribe to the free plan](https://rapidapi.com/OmkarCloud/api/best-google-events-scraper-free-200-calls/pricing) — 200 calls/month, no credit card.
2. [Try the endpoints in the playground](https://rapidapi.com/OmkarCloud/api/best-google-events-scraper-free-200-calls/playground) — every param is pre-filled, so you see real data in one click.
3. Copy the generated code and replace `https://best-google-events-scraper-free-200-calls.p.rapidapi.com` with `http://localhost:8000`. It will now run against your local API.

```python
import requests

# generated by the playground, host swapped for the local API
response = requests.get(
    "http://localhost:8000/search",
    params={"query": "concerts in New York", "date": "this_weekend"},
)
print(response.json())
```

## 💬 Have Questions? We Have Answers.

You're a developer — we know how hard completing a project can be. So we offer full support: just message us and we'll reply ✅ with a solution within 1 working day.

[![Message Us on WhatsApp about Google Events Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/whatsapp-us.png)](https://api.whatsapp.com/send?phone=918178804274&text=I%20need%20help%20using%20the%20Google%20Events%20Scraper%20API.)

[![Ask Us by Email about Google Events Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/ask-on-email.png)](mailto:happy.to.help@omkar.cloud?subject=Help%20with%20Google%20Events%20Scraper%20API&body=I%20need%20help%20using%20the%20Google%20Events%20Scraper%20API.)

## ⚡ Popular Scrapers by Omkar Cloud

- [**Google Maps Scraper (3,100+ GitHub Stars)**](https://github.com/omkarcloud/google-maps-scraper) — type "dentists in New York", get every business as a ready-to-call lead list: phones, emails, websites & reviews. Up to 100K free leads/month.
- [**Google Search Scraper**](https://github.com/omkarcloud/google-scraper) — the full Google results page: organic results, AI Overview, News, Trends & Shopping
- [**G2 Scraper**](https://www.omkar.cloud/tools/g2-scraper) — G2 product details, ratings & AI-found contacts
- [**Website Email Contact Scraper**](https://www.omkar.cloud/tools/website-email-contact-scraper) — emails, phones & socials from any website
- [**AliExpress Scraper**](https://www.omkar.cloud/tools/aliexpress-scraper) — live product details, SKU variants, stock & shipping
- [**Booking Scraper**](https://www.omkar.cloud/tools/booking-scraper) — Booking.com hotels: prices, ratings, rooms & amenities

## ⭐ Love It? [Star It ⭐!](https://github.com/omkarcloud/google-events-scraper)

Star the repo ⭐ and become my star hero!

It's just 1 click, but it means the world to me.

[![Star us on GitHub](https://raw.githubusercontent.com/omkarcloud/google-maps-scraper/master/screenshots/star-us.png)](https://github.com/omkarcloud/google-events-scraper)
