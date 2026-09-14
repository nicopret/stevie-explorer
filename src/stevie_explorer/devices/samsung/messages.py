"""Normalize Samsung envelopes without retaining their raw payloads."""

import json
import re
from collections.abc import Iterator
from typing import Any


def message_nodes(message: Any, depth: int = 0) -> Iterator[dict]:
    # Samsung data may itself be JSON text; bound traversal to envelope fields.
    if depth > 4:
        return
    if isinstance(message, str):
        try:
            message = json.loads(message)
        except (ValueError, RecursionError):
            return
    if isinstance(message, dict):
        yield message
        for key in ("params", "data"):
            yield from message_nodes(message.get(key), depth + 1)


def event_message(message: dict) -> dict:
    return next((node for node in message_nodes(message) if isinstance(node.get("event"), str)), {})


def extract_event_name(message: dict) -> str | None:
    return event_message(message).get("event")


def has_application_list(message: dict) -> bool:
    node = event_message(message)
    if any(item.get("error") is not None for item in message_nodes(message)):
        return False
    data = node.get("data")
    for _ in range(5):
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except (ValueError, RecursionError):
                return False
        if isinstance(data, list):
            return all(
                isinstance(app, dict)
                and type(app.get("appId")) in (str, int)
                and str(app["appId"]).strip() != ""
                and ("name" not in app or isinstance(app["name"], str))
                and ("app_type" not in app or type(app["app_type"]) in (str, int))
                for app in data
            )
        if not isinstance(data, dict):
            return False
        data = data.get("data")
    return False


def diagnostic_identifier(value: Any, secrets: set[str]) -> str | None:
    if not isinstance(value, str):
        return None
    if any(secret and secret in value for secret in secrets):
        return "<redacted>"
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,128}", value):
        return "<invalid>"
    return value
