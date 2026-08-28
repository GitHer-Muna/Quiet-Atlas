"""Read-only atlas listing API Lambda."""
from __future__ import annotations

import base64
import json
import os

import boto3
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key

from common import json_response


def _decode_cursor(value: str | None):
    if not value:
        return None
    try:
        decoded = base64.urlsafe_b64decode(value.encode("ascii") + b"===").decode("utf-8")
        cursor = json.loads(decoded)
        return cursor if isinstance(cursor, dict) else None
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def _encode_cursor(value):
    if not value:
        return None
    raw = json.dumps(value, separators=(",", ":"), default=str).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def handler(event, context):
    query = event.get("queryStringParameters") or {}
    try:
        limit = min(max(int(query.get("limit", "12")), 1), 30)
    except ValueError:
        limit = 12
    try:
        table = boto3.resource("dynamodb").Table(os.environ["ATLAS_TABLE_NAME"])
        query_args = {
            "IndexName": "entryType-createdAt-index",
            "KeyConditionExpression": Key("entryType").eq("atlas"),
            "ScanIndexForward": False,
            "Limit": limit,
        }
        cursor = _decode_cursor(query.get("cursor"))
        if cursor:
            query_args["ExclusiveStartKey"] = cursor
        result = table.query(**query_args)
        return json_response(200, {"items": result.get("Items", []), "nextCursor": _encode_cursor(result.get("LastEvaluatedKey"))})
    except ClientError:
        return json_response(503, {"message": "The atlas is resting for a moment. Please try again shortly.", "code": "atlas_resting"})
