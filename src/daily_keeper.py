"""Scheduled daily keeper Lambda."""
from __future__ import annotations

import logging

from common import AtlasError, choose_small_place, generate_keeper_entry, get_weather, make_entry, store_entry

LOGGER = logging.getLogger(__name__)


def handler(event, context):
    try:
        place = choose_small_place()
        weather = get_weather(place)
        entry = make_entry(place, weather, generate_keeper_entry(place, weather), "daily")
        store_entry(entry)
        LOGGER.info("Added daily entry %s for %s", entry["entryId"], place["placeName"])
        return {"entryId": entry["entryId"], "placeName": entry["placeName"], "status": "created"}
    except AtlasError:
        LOGGER.exception("Daily keeper could not complete its round")
        raise
