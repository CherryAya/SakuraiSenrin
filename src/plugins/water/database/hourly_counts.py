"""Compact codecs for water daily summary hourly counts."""

from __future__ import annotations

from collections.abc import Iterable
import json
import struct
from typing import Any

from sqlalchemy.types import LargeBinary, TypeDecorator

_HOURS_PER_DAY = 24
_SPARSE_THRESHOLD = 12
_FORMAT_PLAIN_U16 = 0
_FORMAT_SPARSE_U16 = 1
_PLAIN_HEADER = bytes([_FORMAT_PLAIN_U16])
_SPARSE_HEADER = bytes([_FORMAT_SPARSE_U16])


def normalize_hourly_counts(hourly_counts: Iterable[Any]) -> list[int]:
    values = [int(item) for item in list(hourly_counts)[:_HOURS_PER_DAY]]
    if len(values) < _HOURS_PER_DAY:
        values.extend([0] * (_HOURS_PER_DAY - len(values)))
    return values


def encode_hourly_counts(hourly_counts: Iterable[Any]) -> bytes:
    values = normalize_hourly_counts(hourly_counts)
    non_zero = [(hour, count) for hour, count in enumerate(values) if count > 0]
    if len(non_zero) <= _SPARSE_THRESHOLD:
        payload = bytearray(_SPARSE_HEADER)
        payload.append(len(non_zero))
        for hour, count in non_zero:
            payload.append(hour)
            payload.extend(struct.pack("<H", count))
        return bytes(payload)
    payload = bytearray(_PLAIN_HEADER)
    for count in values:
        payload.extend(struct.pack("<H", count))
    return bytes(payload)


def decode_hourly_counts(value: Any) -> list[int]:
    if value is None:
        return [0] * _HOURS_PER_DAY
    if isinstance(value, list):
        return normalize_hourly_counts(value)
    if isinstance(value, str):
        return normalize_hourly_counts(json.loads(value))
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, bytearray):
        value = bytes(value)
    if not isinstance(value, bytes):
        raise TypeError(f"unsupported hourly_counts payload: {type(value)!r}")
    if not value:
        return [0] * _HOURS_PER_DAY

    fmt = value[0]
    if fmt == _FORMAT_PLAIN_U16:
        body = value[1:]
        if len(body) != _HOURS_PER_DAY * 2:
            raise ValueError("invalid plain hourly_counts payload length")
        return list(struct.unpack("<24H", body))
    if fmt == _FORMAT_SPARSE_U16:
        if len(value) < 2:
            raise ValueError("invalid sparse hourly_counts payload length")
        count = value[1]
        expected = 2 + count * 3
        if len(value) != expected:
            raise ValueError("invalid sparse hourly_counts payload length")
        decoded = [0] * _HOURS_PER_DAY
        offset = 2
        for _ in range(count):
            hour = value[offset]
            offset += 1
            bucket = struct.unpack("<H", value[offset : offset + 2])[0]
            offset += 2
            if hour >= _HOURS_PER_DAY:
                raise ValueError("invalid sparse hourly_counts hour")
            decoded[hour] = int(bucket)
        return decoded
    try:
        return normalize_hourly_counts(json.loads(value.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("unknown hourly_counts payload format") from exc


def merge_hourly_counts(
    payloads: Iterable[Iterable[Any] | bytes | str | list[int]],
) -> list[int]:
    merged = [0] * _HOURS_PER_DAY
    for payload in payloads:
        decoded = decode_hourly_counts(payload)
        for hour, count in enumerate(decoded):
            merged[hour] += int(count)
    return merged


class HourlyCountsType(TypeDecorator[list[int]]):
    impl = LargeBinary
    cache_ok = True

    def process_bind_param(
        self,
        value: list[int] | bytes | bytearray | memoryview | None,
        dialect: Any,
    ) -> bytes:
        _ = dialect
        if value is None:
            return encode_hourly_counts([])
        if isinstance(value, memoryview):
            return value.tobytes()
        if isinstance(value, bytearray):
            return bytes(value)
        if isinstance(value, bytes):
            return value
        return encode_hourly_counts(value)

    def process_result_value(self, value: Any, dialect: Any) -> list[int]:
        _ = dialect
        return decode_hourly_counts(value)


__all__ = [
    "HourlyCountsType",
    "decode_hourly_counts",
    "encode_hourly_counts",
    "merge_hourly_counts",
    "normalize_hourly_counts",
]
