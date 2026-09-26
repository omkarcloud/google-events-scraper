"""Marshmallow request schemas for every /google-events/* route.

Generic fields come from the shared schema_fields.py and the Google Search
schemas (country / language / query-or-link); this module adds the event
references. Every schema's load() output is the kwargs dict its endpoint
function takes.

ONE param per input (tripadvisor QueryOrLinkField convention, never a
sibling `url` / `id` pair): `query` takes a search term OR a
google.com/search link (whose gl / hl fill country / language when those
are absent), `event` an event id OR the `link` of a search result OR an
event token (incl. the base64 event_id other Google Events APIs return),
`events` a comma list of those, `venue` / `artist` a name OR a
google.com/search link naming it.
"""
from marshmallow import ValidationError, fields, pre_load, validate

from google_events import events as E, refs
from google_search.schemas import CountryField, LanguageField, SearchQueryField
from schema_fields import BaseSchema, ChoiceField, Flag, LimitField, QueryField, RefField, StrippedString

MAX_BATCH = 10


class EventRefField(RefField):
    resolver = staticmethod(refs.resolve_event)


class VenueField(RefField):
    resolver = staticmethod(lambda v: refs.resolve_name(v, "venue"))

    def __init__(self, **kwargs):
        kwargs.setdefault("validate", validate.Length(min=1, max=150))
        super().__init__(**kwargs)


class ArtistField(RefField):
    resolver = staticmethod(lambda v: refs.resolve_name(v, "artist"))

    def __init__(self, **kwargs):
        kwargs.setdefault("validate", validate.Length(min=1, max=150))
        super().__init__(**kwargs)


class EventListField(fields.Field):
    """'id1,id2,<event link>' -> [{"id", "token", "query"}, …] (resolved,
    deduped, 1..MAX_BATCH)."""

    def __init__(self, **kwargs):
        kwargs.setdefault("required", True)
        super().__init__(**kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        out, seen = [], set()
        for item in refs.split_events(value):
            try:
                ref = refs.resolve_event(item)
            except ValueError as e:
                raise ValidationError(f"'{item[:80]}': {e}")
            key = ref["id"] or ref["token"]
            if key not in seen:
                seen.add(key)
                out.append(ref)
        if not out:
            raise ValidationError("Must list at least one event id or event link (comma-separated).")
        if len(out) > MAX_BATCH:
            raise ValidationError(f"At most {MAX_BATCH} events.")
        return out


class LocationText(StrippedString):
    """Optional place text ("Austin, TX"); blank -> None. The length check is
    done here: a marshmallow validator would also run on the None a blank
    value becomes, and len(None) raises."""
    MAX = 100

    def __init__(self, **kwargs):
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        if value is not None and len(value) > self.MAX:
            raise ValidationError(f"Longer than maximum length {self.MAX}.")
        return value


class _Locale(BaseSchema):
    country = CountryField()
    language = LanguageField()

    @pre_load
    def from_link(self, data, **kwargs):
        """A pasted google.com link fills country / language."""
        link_params = {}
        for key in ("query", "event", "venue", "artist"):
            link_params = refs.params_of(data.get(key) or "")
            if link_params:
                break
        if not link_params:
            return data
        data = dict(data)
        if link_params.get("gl") and not data.get("country"):
            data["country"] = link_params["gl"]
        if link_params.get("hl") and not data.get("language"):
            data["language"] = link_params["hl"].split("-")[0]
        return data


class SearchSchema(_Locale):
    query = SearchQueryField()
    location = LocationText()
    date = ChoiceField(E.DATES, load_default="any")
    online_only = Flag()
    include_details = Flag(load_default=False)

    @pre_load
    def date_alias(self, data, **kwargs):
        """The short names other Google Events APIs use (week, weekend, month)."""
        value = (data.get("date") or "").strip().lower()
        if value in E.DATE_ALIASES:
            data = dict(data)
            data["date"] = E.DATE_ALIASES[value]
        return data


class EventSchema(_Locale):
    event = EventRefField()


class EventBatchSchema(_Locale):
    events = EventListField()


class VenueEventsSchema(_Locale):
    venue = VenueField()
    location = LocationText()


class ArtistEventsSchema(_Locale):
    artist = ArtistField()
    location = LocationText()


class AutocompleteSchema(_Locale):
    query = QueryField(max_length=100)
    limit = LimitField(default=10, max_size=20)


__all__ = ["SearchSchema", "EventSchema", "EventBatchSchema", "VenueEventsSchema", "ArtistEventsSchema",
           "AutocompleteSchema", "ValidationError"]
