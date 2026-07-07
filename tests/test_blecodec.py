"""TDD for the BLE wire format shared by the Pi peripheral and the iOS app.

The detector snapshot is encoded to a compact JSON payload with short keys so a
single BLE notification stays within one negotiated MTU (~180 bytes on iOS).
Both sides must agree on these keys, so they're pinned here.
"""

from __future__ import annotations

import json

from uwb_explorer.blecodec import encode_state, KEY_MAP


def _snap(**over):
    base = {
        "status": "live", "level": "high", "hits": 142, "total": 9001,
        "peak": 210, "decoded": 0, "channel": 9, "pcode": 11,
        "delta": {"SFDD": 1}, "history": list(range(120)),
    }
    base.update(over)
    return base


def test_encodes_to_compact_json_bytes():
    payload = encode_state(_snap())
    assert isinstance(payload, bytes)
    obj = json.loads(payload)
    # every mapped short key is present
    assert set(obj) == set(KEY_MAP.values())


def test_short_keys_carry_the_right_values():
    obj = json.loads(encode_state(_snap()))
    assert obj["s"] == "live"      # status
    assert obj["l"] == "high"      # level
    assert obj["h"] == 142         # hits
    assert obj["t"] == 9001        # total
    assert obj["p"] == 210         # peak
    assert obj["d"] == 0           # decoded
    assert obj["c"] == 9           # channel
    assert obj["k"] == 11          # pcode


def test_payload_fits_one_ble_mtu():
    # even with big/negative fields the payload must stay small; history and
    # delta are intentionally NOT sent (the app builds its own sparkline).
    payload = encode_state(_snap(hits=999999, total=999999999, peak=999999))
    assert len(payload) < 180
    assert b"history" not in payload and b"SFDD" not in payload


def test_handles_null_channel_and_waiting_status():
    obj = json.loads(encode_state(_snap(status="waiting", channel=None, pcode=None)))
    assert obj["s"] == "waiting"
    assert obj["c"] is None and obj["k"] is None


def test_missing_fields_default_to_none():
    obj = json.loads(encode_state({"status": "error"}))
    assert obj["s"] == "error"
    assert obj["h"] is None  # absent hits -> null, not a crash
