"""TDD for the phone dashboard HTTP layer.

These run a real loopback server on an ephemeral port (no hardware, no board)
and exercise it with urllib, plus the pure `poll_once` seam that drives the
detector state from a device.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

from uwb_explorer.web import DashboardServer, poll_once
from uwb_explorer.webmodel import DetectorState


def _serve(snapshot):
    srv = DashboardServer(snapshot, host="127.0.0.1", port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _fetch(port, path):
    url = f"http://127.0.0.1:{port}{path}"
    with urllib.request.urlopen(url, timeout=3) as r:
        return r.status, r.read().decode(), r.headers.get("Content-Type", "")


def test_api_state_returns_snapshot_json():
    snap = {"hits": 3, "level": "low", "history": [0, 1, 2], "status": "live"}
    srv = _serve(lambda: snap)
    try:
        status, body, ctype = _fetch(srv.port, "/api/state")
        assert status == 200
        assert "json" in ctype
        assert json.loads(body) == snap
    finally:
        srv.shutdown()


def test_root_serves_self_contained_html():
    srv = _serve(lambda: {})
    try:
        status, body, ctype = _fetch(srv.port, "/")
        assert status == 200
        assert "html" in ctype
        assert "UWB" in body
        # must be self-contained (no external requests) for the con hotspot
        assert "http://" not in body.replace("http://127.0.0.1", "")
    finally:
        srv.shutdown()


def test_unknown_path_is_404():
    srv = _serve(lambda: {})
    try:
        try:
            _fetch(srv.port, "/nope")
            raise AssertionError("expected HTTP 404")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        srv.shutdown()


def test_poll_once_drives_state_from_device():
    class FakeDev:
        def __init__(self):
            self.n = 0

        def get_lstat(self):
            self.n += 1
            return {"SFDD": self.n * 5}

    state = DetectorState()
    dev = FakeDev()
    poll_once(dev, state)          # baseline
    snap = poll_once(dev, state)   # +5 SFDD
    assert snap["hits"] == 5


def test_poll_once_survives_none_lstat():
    class DeadDev:
        def get_lstat(self):
            return None

    state = DetectorState()
    snap = poll_once(DeadDev(), state)  # must not raise
    assert snap["hits"] == 0
