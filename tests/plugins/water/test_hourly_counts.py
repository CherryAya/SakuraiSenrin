from __future__ import annotations

import json

from src.plugins.water.database.hourly_counts import (
    decode_hourly_counts,
    encode_hourly_counts,
    merge_hourly_counts,
)


def test_hourly_counts_sparse_roundtrip() -> None:
    payload = [0] * 24
    payload[2] = 5
    payload[9] = 12
    payload[23] = 1

    encoded = encode_hourly_counts(payload)

    assert encoded[0] == 1
    assert decode_hourly_counts(encoded) == payload


def test_hourly_counts_plain_roundtrip() -> None:
    payload = list(range(24))

    encoded = encode_hourly_counts(payload)

    assert encoded[0] == 0
    assert decode_hourly_counts(encoded) == payload


def test_hourly_counts_supports_legacy_json_payload() -> None:
    payload = [hour % 3 for hour in range(24)]

    assert decode_hourly_counts(json.dumps(payload)) == payload


def test_merge_hourly_counts_accumulates_payloads() -> None:
    left = [1] * 24
    right = [2] * 24

    merged = merge_hourly_counts([encode_hourly_counts(left), right])

    assert merged == [3] * 24
