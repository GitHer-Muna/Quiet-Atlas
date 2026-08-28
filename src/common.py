"""Shared services for The Quiet Atlas Lambda handlers."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import random
import re
import time
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

try:
    import boto3
    from botocore.exceptions import ClientError
except ModuleNotFoundError:  # boto3 is provided by the Lambda runtime.
    class ClientError(Exception):
        pass

    class _MissingBoto3:
        @staticmethod
        def client(*args, **kwargs):
            raise RuntimeError("boto3 is required for AWS calls")

        @staticmethod
        def resource(*args, **kwargs):
            raise RuntimeError("boto3 is required for AWS calls")

    boto3 = _MissingBoto3()

LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(os.getenv("LOG_LEVEL", "INFO"))

HTTP_TIMEOUT_SECONDS = 8
WEATHER_CODES = {
    0: "clear skies",
    1: "mostly clear skies",
    2: "partly cloudy skies",
    3: "overcast skies",
    45: "mist",
    48: "rime fog",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    80: "rain showers",
    81: "rain showers",
    82: "heavy rain showers",
    95: "a thunderstorm",
    96: "a thunderstorm with hail",
    99: "a thunderstorm with hail",
}


class AtlasError(Exception):
    """An expected, user-safe application error."""


class RateLimitExceeded(AtlasError):
    """The visitor has used all request credits for the current UTC day."""


def utc_now() -> datetime:
    return datetime.now(UTC)


def http_get_json(url: str, opener: Callable[..., Any] | None = None) -> dict[str, Any]:
    """GET JSON with one retry for transient provider/network errors."""
    request_opener = opener or urllib.request.urlopen
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            request = urllib.request.Request(
                url,
                headers={"Accept": "application/json", "User-Agent": "quiet-atlas/1.0"},
            )
            with request_opener(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                status = getattr(response, "status", 200)
                if status >= 500:
                    raise AtlasError(f"provider returned {status}")
                return json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError, AtlasError) as error:
            last_error = error
            if attempt == 0:
                time.sleep(0.15)
    raise AtlasError("an outside source did not answer") from last_error


def _require_geonames_username() -> str:
    username = os.getenv("GEONAMES_USERNAME", "").strip()
    if not username:
        raise AtlasError("the atlas has no mapkeeper username configured")
    return username


def _place_from_geonames(item: dict[str, Any]) -> dict[str, Any]:
    try:
        return {
            "placeName": str(item["name"]).strip(),
            "country": str(item.get("countryName") or item.get("countryCode") or "Unknown").strip(),
            "lat": float(item["lat"]),
            "lon": float(item["lng"]),
            "population": int(item.get("population") or 0),
            "timezone": str((item.get("timezone") or {}).get("timeZoneId") or "UTC"),
            "geonameId": str(item.get("geonameId") or ""),
        }
    except (KeyError, TypeError, ValueError) as error:
        raise AtlasError("the map returned an unreadable place") from error


def search_place(place_name: str) -> dict[str, Any]:
    """Resolve a visitor's place name, preferring a populated place."""
    cleaned = " ".join(place_name.split())
    if not 2 <= len(cleaned) <= 120:
        raise AtlasError("please give the atlas a place name between 2 and 120 characters")
    query = urllib.parse.urlencode(
        {
            "q": cleaned,
            "maxRows": 10,
            "type": "json",
            "featureClass": "P",
            "orderby": "relevance",
            "username": _require_geonames_username(),
        }
    )
    result = http_get_json(f"https://api.geonames.org/searchJSON?{query}")
    matches = result.get("geonames") or []
    if not matches:
        raise AtlasError("the atlas could not find that place; try a town and country")
    return _place_from_geonames(matches[0])


def choose_small_place() -> dict[str, Any]:
    """Choose a random small populated place from GeoNames search results."""
    # GeoNames search is deliberately broad here; filtering population locally keeps
    # the selection transparent and avoids depending on an obscure provider endpoint.
    query = urllib.parse.urlencode(
        {
            "q": random.choice(["a", "e", "i", "o", "u"]),
            "maxRows": 100,
            "type": "json",
            "featureClass": "P",
            "username": _require_geonames_username(),
        }
    )
    result = http_get_json(f"https://api.geonames.org/searchJSON?{query}")
    candidates = [item for item in (result.get("geonames") or []) if 0 < int(item.get("population") or 0) < 5000]
    if not candidates:
        raise AtlasError("the atlas could not find a small town today")
    return _place_from_geonames(random.choice(candidates))


def get_weather(place: dict[str, Any]) -> dict[str, Any]:
    query = urllib.parse.urlencode(
        {
            "latitude": place["lat"],
            "longitude": place["lon"],
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m",
            "timezone": "auto",
            "forecast_days": 1,
        }
    )
    result = http_get_json(f"https://api.open-meteo.com/v1/forecast?{query}")
    current = result.get("current") or {}
    if "temperature_2m" not in current:
        raise AtlasError("the weather station is quiet just now")
    code = int(current.get("weather_code", -1))
    return {
        "temperatureC": current.get("temperature_2m"),
        "apparentTemperatureC": current.get("apparent_temperature"),
        "humidityPercent": current.get("relative_humidity_2m"),
        "windKmh": current.get("wind_speed_10m"),
        "weatherCode": code,
        "condition": WEATHER_CODES.get(code, "unfamiliar weather"),
        "observedAt": current.get("time"),
        "timezone": result.get("timezone") or place.get("timezone") or "UTC",
    }


def keeper_word_count(entry: str) -> int:
    return len(re.findall(r"\b[\w’'-]+\b", entry))


def generate_keeper_entry(place: dict[str, Any], weather: dict[str, Any]) -> str:
    """Ask Bedrock for a grounded entry and accept only 150–220 words."""
    model_arn = os.getenv("BEDROCK_MODEL_ARN", "").strip()
    if not model_arn:
        raise AtlasError("the atlas has no storyteller configured")
    bedrock = boto3.client("bedrock-runtime", region_name=os.getenv("AWS_REGION"))
    prompt = f"""You are the fictional keeper of a quiet place. Write a warm, observant almanac entry of 150 to 220 words about this real place.

Place: {place['placeName']}, {place['country']}
Population: {place['population']}
Coordinates: {place['lat']:.4f}, {place['lon']:.4f}
Local time zone: {weather['timezone']}
Observed weather: {weather['temperatureC']}°C, feels like {weather['apparentTemperatureC']}°C, {weather['condition']}, wind {weather['windKmh']} km/h, observed {weather['observedAt']}

Write in first person as a long-time keeper. Be specific but do not invent historical claims, businesses, landmarks, or local customs. Treat the weather and geographic facts as ground truth. Do not add a title, bullet list, markdown, or preamble; return only the almanac prose. Count the words before returning and keep the prose between 150 and 220 words."""
    for attempt in range(2):
        try:
            response = bedrock.converse(
                modelId=model_arn,
                system=[{"text": "You write concise literary nonfiction grounded in supplied facts."}],
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": 420, "temperature": 0.75},
            )
            content = response.get("output", {}).get("message", {}).get("content", [])
            entry = " ".join(part.get("text", "") for part in content if part.get("text")).strip()
        except (ClientError, OSError, KeyError, TypeError) as error:
            LOGGER.exception("Bedrock generation failed")
            raise AtlasError("the keeper is still sharpening a pencil") from error
        count = keeper_word_count(entry)
        if 150 <= count <= 220:
            return entry
        LOGGER.warning("Bedrock returned %s words on attempt %s", count, attempt + 1)
        prompt += " The previous draft was outside the range. Return only a new draft between 150 and 220 words."
    raise AtlasError("the keeper needs another moment to fit the page")


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:70] or "unnamed-place"


def make_entry(place: dict[str, Any], weather: dict[str, Any], keeper_entry: str, source: str) -> dict[str, Any]:
    now = utc_now()
    created_at = now.isoformat().replace("+00:00", "Z")
    return {
        "entryId": f"{now.date().isoformat()}#{slugify(place['placeName'])}#{uuid.uuid4().hex[:8]}",
        "entryType": "atlas",
        "placeName": place["placeName"],
        "country": place["country"],
        "lat": place["lat"],
        "lon": place["lon"],
        "population": place["population"],
        "timezone": weather["timezone"],
        "weatherSnapshot": weather,
        "keeperEntry": keeper_entry,
        "createdAt": created_at,
        "source": source,
    }


def store_entry(entry: dict[str, Any]) -> None:
    boto3.resource("dynamodb").Table(os.environ["ATLAS_TABLE_NAME"]).put_item(Item=entry)


def request_ip(event: dict[str, Any]) -> str:
    return str(
        (((event.get("requestContext") or {}).get("http") or {}).get("sourceIp"))
        or "unknown"
    )


def consume_request_slot(event: dict[str, Any]) -> None:
    ip = request_ip(event)
    salt = os.getenv("THROTTLE_HASH_SALT", "quiet-atlas-public")
    ip_hash = hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()
    date = utc_now().date().isoformat()
    key = f"{ip_hash}#{date}"
    limit = int(os.getenv("THROTTLE_LIMIT", "5"))
    expires_at = int((utc_now() + timedelta(days=2)).timestamp())
    try:
        boto3.resource("dynamodb").Table(os.environ["THROTTLE_TABLE_NAME"]).update_item(
            Key={"throttleKey": key},
            UpdateExpression="SET expiresAt = if_not_exists(expiresAt, :expires) ADD requestCount :one",
            ConditionExpression="attribute_not_exists(requestCount) OR requestCount < :limit",
            ExpressionAttributeValues={":expires": expires_at, ":one": 1, ":limit": limit},
        )
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            raise RateLimitExceeded("The keeper's desk is closed for today from this address. Please return tomorrow.") from error
        raise


def json_response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "cache-control": "no-store" if status_code >= 400 else "public, max-age=30",
        },
        "body": json.dumps(body, default=str),
    }


def parse_body(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as error:
        raise AtlasError("please send a JSON place request") from error
    if not isinstance(data, dict):
        raise AtlasError("please send a JSON object")
    return data
