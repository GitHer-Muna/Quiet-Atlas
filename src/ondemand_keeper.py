"""On-demand keeper API Lambda."""
from __future__ import annotations

import logging

from common import (
    AtlasError,
    RateLimitExceeded,
    consume_request_slot,
    generate_keeper_entry,
    get_weather,
    json_response,
    make_entry,
    parse_body,
    search_place,
    store_entry,
)

LOGGER = logging.getLogger(__name__)


def handler(event, context):
    try:
        consume_request_slot(event)
        data = parse_body(event)
        place_name = data.get("placeName") or data.get("place") or ""
        place = search_place(str(place_name))
        weather = get_weather(place)
        entry = make_entry(place, weather, generate_keeper_entry(place, weather), "requested")
        store_entry(entry)
        return json_response(201, {"entry": entry})
    except RateLimitExceeded as error:
        return json_response(429, {"message": str(error), "code": "daily_limit_reached"})
    except AtlasError as error:
        LOGGER.warning("On-demand request could not be completed: %s", error)
        return json_response(400, {"message": str(error), "code": "atlas_resting"})
    except Exception:
        LOGGER.exception("Unexpected on-demand keeper failure")
        return json_response(503, {"message": "The atlas is resting for a moment. Please try again shortly.", "code": "atlas_resting"})
