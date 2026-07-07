"""Compact wire format for the UWB state over BLE.

The detector snapshot is reduced to short-keyed JSON so one notification fits a
single negotiated MTU (~180 bytes on iOS). History/deltas are deliberately
omitted — the iOS app accumulates its own sparkline from the `hits` stream.
The Pi peripheral (uwb_explorer/ble.py) and the iOS app must agree on KEY_MAP.
"""

from __future__ import annotations

import json

# full snapshot field -> short BLE key
KEY_MAP = {
    "status": "s",
    "level": "l",
    "hits": "h",
    "total": "t",
    "peak": "p",
    "decoded": "d",
    "channel": "c",
    "pcode": "k",
}


def encode_state(snapshot: dict) -> bytes:
    out = {short: snapshot.get(full) for full, short in KEY_MAP.items()}
    return json.dumps(out, separators=(",", ":")).encode("utf-8")
