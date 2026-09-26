"""Cache TTL per /google-events/* endpoint (cache.py, keyed on the validated
params — marshmallow fills the defaults, so `?date=any` and no `date` share
a row; an event's id-form and link-form share a row only when they resolve
to the same {id, token, query}).

A results page costs an identity use and the carousel reshuffles within a
day ("today" moves at midnight), so searches cache for a couple of hours;
an event's record (schedule, venue, sellers) rarely changes once listed,
but ticket prices do; suggestions barely move."""
from datetime import timedelta

SEARCH_CACHE = timedelta(hours=2)
DETAILS_CACHE = timedelta(hours=6)
PANEL_CACHE = timedelta(hours=3)
AUTOCOMPLETE_CACHE = timedelta(hours=12)
