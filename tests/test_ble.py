"""TDD for the BLE peripheral's pure parts (no BlueZ needed).

The transport itself is exercised as an integration test on the Pi against a
real central; here we pin the read-handler behaviour and the UUIDs the iOS app
depends on. ble.py imports `bless` lazily, so this imports cleanly without it.
"""

from __future__ import annotations

import json

from uwb_explorer.ble import build_read_handler, SERVICE_UUID, CHAR_UUID
from uwb_explorer.webmodel import DetectorState


def test_read_handler_returns_live_state_payload():
    s = DetectorState()
    s.update({"SFDD": 0})
    s.update({"SFDD": 5})
    s.set_status("live")
    s.set_config(channel=9, pcode=11)
    payload = bytes(build_read_handler(s)(None))
    obj = json.loads(payload)
    assert obj["h"] == 5
    assert obj["s"] == "live"
    assert obj["c"] == 9 and obj["k"] == 11


def test_read_handler_reflects_updates_each_call():
    s = DetectorState()
    s.update({"SFDD": 0})
    h = build_read_handler(s)
    s.update({"SFDD": 3})
    assert json.loads(bytes(h(None)))["h"] == 3
    s.update({"SFDD": 100})
    assert json.loads(bytes(h(None)))["h"] == 97


def test_uuids_are_distinct_128bit():
    assert SERVICE_UUID != CHAR_UUID
    assert len(SERVICE_UUID) == 36 and len(CHAR_UUID) == 36
