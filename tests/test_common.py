from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import common  # noqa: E402


class CommonServiceTests(unittest.TestCase):
    def test_place_result_is_normalized(self):
        with patch.dict(os.environ, {}, clear=False), patch.object(
            common,
            "http_get_json",
            return_value={
                "results": [
                    {
                        "name": "Aldea",
                        "country": "Exampleland",
                        "country_code": "EX",
                        "latitude": 41.25,
                        "longitude": -7.5,
                        "population": 412,
                        "timezone": "Europe/Example",
                        "id": 7,
                    }
                ]
            },
        ) as get_json:
            place = common.search_place(" Aldea, Exampleland ")
        self.assertEqual(place["placeName"], "Aldea")
        self.assertEqual(place["country"], "Exampleland")
        self.assertEqual(place["lat"], 41.25)
        self.assertEqual(place["population"], 412)
        self.assertIn("geocoding-api.open-meteo.com", get_json.call_args.args[0])

    def test_weather_snapshot_keeps_ground_truth_fields(self):
        with patch.object(
            common,
            "http_get_json",
            return_value={
                "timezone": "Europe/Example",
                "current": {
                    "temperature_2m": 12.4,
                    "apparent_temperature": 11.1,
                    "relative_humidity_2m": 76,
                    "weather_code": 61,
                    "wind_speed_10m": 9.2,
                    "time": "2026-08-28T08:00",
                },
            },
        ):
            snapshot = common.get_weather({"lat": 1, "lon": 2, "timezone": "UTC"})
        self.assertEqual(snapshot["condition"], "light rain")
        self.assertEqual(snapshot["temperatureC"], 12.4)
        self.assertEqual(snapshot["observedAt"], "2026-08-28T08:00")
        self.assertEqual(snapshot["timezone"], "Europe/Example")

    def test_generation_uses_configured_profile_and_returns_text(self):
        bedrock = MagicMock()
        generated = " ".join(f"word{i}" for i in range(150))
        bedrock.converse.return_value = {"output": {"message": {"content": [{"text": generated}]}}}
        with patch.dict(os.environ, {"BEDROCK_MODEL_ARN": "arn:aws:bedrock:us-east-1:123456789012:inference-profile/test"}), patch(
            "common.boto3.client", return_value=bedrock
        ):
            result = common.generate_keeper_entry(
                {"placeName": "Aldea", "country": "Exampleland", "population": 4, "lat": 1, "lon": 2},
                {"timezone": "UTC", "temperatureC": 12, "apparentTemperatureC": 11, "condition": "clear skies", "windKmh": 2, "observedAt": "now"},
            )
        self.assertEqual(common.keeper_word_count(result), 150)
        self.assertEqual(bedrock.converse.call_args.kwargs["modelId"], "arn:aws:bedrock:us-east-1:123456789012:inference-profile/test")
        self.assertIn("Aldea", bedrock.converse.call_args.kwargs["messages"][0]["content"][0]["text"])

    def test_generation_rejects_two_out_of_range_drafts(self):
        bedrock = MagicMock()
        bedrock.converse.return_value = {"output": {"message": {"content": [{"text": "Too short."}]}}}
        with patch.dict(os.environ, {"BEDROCK_MODEL_ARN": "arn:aws:bedrock:us-east-1:123456789012:inference-profile/test"}), patch(
            "common.boto3.client", return_value=bedrock
        ):
            with self.assertRaises(common.AtlasError):
                common.generate_keeper_entry(
                    {"placeName": "Aldea", "country": "Exampleland", "population": 4, "lat": 1, "lon": 2},
                    {"timezone": "UTC", "temperatureC": 12, "apparentTemperatureC": 11, "condition": "clear skies", "windKmh": 2, "observedAt": "now"},
                )
        self.assertEqual(bedrock.converse.call_count, 2)

    def test_throttle_uses_hashed_ip_and_atomic_limit_condition(self):
        table = MagicMock()
        with patch.dict(os.environ, {"THROTTLE_TABLE_NAME": "throttle", "THROTTLE_LIMIT": "5", "THROTTLE_HASH_SALT": "test-salt"}), patch(
            "common.boto3.resource", return_value=MagicMock(Table=MagicMock(return_value=table))
        ):
            common.consume_request_slot({"requestContext": {"http": {"sourceIp": "192.0.2.4"}}})
        kwargs = table.update_item.call_args.kwargs
        throttle_key = kwargs["Key"]["throttleKey"]
        self.assertEqual(len(throttle_key.split("#")[0]), 64)  # SHA-256 is hex and stable, raw IP is absent.
        self.assertNotIn("192.0.2.4", throttle_key)
        self.assertIn("requestCount < :limit", kwargs["ConditionExpression"])
        self.assertEqual(kwargs["ExpressionAttributeValues"][":limit"], 5)

    def test_make_entry_contains_source_and_weather_snapshot(self):
        entry = common.make_entry(
            {"placeName": "Aldea", "country": "Exampleland", "lat": 1, "lon": 2, "population": 10},
            {"temperatureC": 10, "condition": "clear skies", "timezone": "UTC"},
            "A small page.",
            "requested",
        )
        self.assertTrue(entry["entryId"].startswith("20"))
        self.assertEqual(entry["source"], "requested")
        self.assertEqual(entry["weatherSnapshot"]["temperatureC"], 10)
        self.assertEqual(entry["entryType"], "atlas")


if __name__ == "__main__":
    unittest.main()
