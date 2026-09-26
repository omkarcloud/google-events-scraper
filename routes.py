"""The 6 Google Events endpoints. Every path is served with and without the
`/google-events` prefix, so code generated against the hosted API on
RapidAPI (paths like /search) runs unchanged against this server.

Params are validated by the marshmallow schemas in google_events/schemas.py
(the same ones the hosted API uses); failures map to HTTP like this:
bad params -> 400, not found -> 404, Google blocked / upstream error -> 502,
anything else -> 500.
"""
import json

from bottle import request, response, route

from google_events import events, schemas
from schema_fields import load_query
from scraper_errors import BadRequest, Blocked, NotFound, UpstreamError

PREFIX = "/google-events"

# (public path, schema, function) — in the order of the docs
ENDPOINTS = [
    ("/details", schemas.EventSchema, events.details),
    ("/autocomplete", schemas.AutocompleteSchema, events.autocomplete),
    ("/search", schemas.SearchSchema, events.search),
    ("/details/batch", schemas.EventBatchSchema, events.batch),
    ("/artist-events", schemas.ArtistEventsSchema, events.artist_events),
    ("/venue-events", schemas.VenueEventsSchema, events.venue_events),
]


def json_response(data, status=200):
    response.status = status
    response.content_type = "application/json"
    return json.dumps(data, ensure_ascii=False)


def query_dict():
    """The query as unicode strings (bottle 0.12's .get() hands back latin-1
    decoded bytes, so a UTF-8 "Amélie" would arrive as "AmÃ©lie")."""
    return {key: request.query.getunicode(key) for key in request.query.keys()}


def handle(path, schema_cls, impl):
    label = f"google-events {path.strip('/')}"
    data, error = load_query(schema_cls, query_dict())
    if error:
        return json_response(error, 400)
    try:
        result = impl(**data)
    except ValueError as e:
        return json_response({"error": str(e)}, 400)
    except BadRequest as e:
        return json_response({"error": f"google-events rejected the request: {e}"}, 400)
    except NotFound as e:
        return json_response({"error": str(e) or "not found"}, 404)
    except Blocked as e:
        return json_response({"error": f"google-events blocked the request, retry later: {e}"}, 502)
    except UpstreamError as e:
        return json_response({"error": f"{label} failed: {e}"}, 502)
    except Exception as e:
        return json_response({"error": f"{label} failed: {type(e).__name__}: {e}"}, 500)
    return json_response(result)


def mount(path, schema_cls, impl):
    """Serve one endpoint at /path and /google-events/path."""
    def handler():
        return handle(path, schema_cls, impl)
    handler.__name__ = "google_events_" + path.strip("/").replace("/", "_").replace("-", "_")
    route(path, method="GET")(handler)
    route(PREFIX + path, method="GET")(handler)


for _path, _schema, _impl in ENDPOINTS:
    mount(_path, _schema, _impl)


@route("/")
@route("/health")
def health():
    return json_response({"status": "ok", "endpoints": [p for p, *_ in ENDPOINTS]})
