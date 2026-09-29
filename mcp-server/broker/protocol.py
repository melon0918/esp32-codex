"""Versioned JSON messages framed as bytes; pickle is deliberately unsupported."""

from __future__ import annotations

import json
import struct
from typing import Any

PROTOCOL_VERSION = 4
MAX_MESSAGE_BYTES = 1024 * 1024
# Large file requests/responses are split into ordinary bounded JSON frames.
MAX_LOGICAL_BYTES = 2 * MAX_MESSAGE_BYTES
CHUNK_BYTES = 384 * 1024
HEADER = struct.Struct("!I")


class ProtocolError(RuntimeError):
    pass


def serialize_message(message: dict[str, Any], *, limit: int = MAX_MESSAGE_BYTES) -> bytes:
    if not isinstance(message, dict):
        raise ProtocolError("message must be a JSON object")
    try:
        payload = json.dumps(
            message, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ProtocolError("message is not valid UTF-8 JSON") from exc
    if not payload or len(payload) > limit:
        raise ProtocolError("message length is outside the allowed limit")
    return payload


def encode_message(message: dict[str, Any]) -> bytes:
    payload = serialize_message(message)
    return HEADER.pack(len(payload)) + payload


def decode_message(payload: bytes, *, limit: int = MAX_MESSAGE_BYTES) -> dict[str, Any]:
    if not payload or len(payload) > limit:
        raise ProtocolError("message length is outside the allowed limit")
    try:
        value = json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non-standard JSON constant")),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProtocolError("message is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ProtocolError("message must be a JSON object")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value
