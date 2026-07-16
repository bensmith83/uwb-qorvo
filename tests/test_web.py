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

import uwb_explorer.device as devmod
import uwb_explorer.serialport as sp
import uwb_explorer.web as webmod
from uwb_explorer.experiments.control import Dispatcher, parse_command
from uwb_explorer.web import DashboardServer, poll_once
from uwb_explorer.webmodel import DetectorState


def _serve(snapshot):
    srv = DashboardServer(snapshot, host="127.0.0.1", port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _serve_ctl(dispatcher, snapshot=None):
    """A loopback server wired to an experiment dispatcher (the downlink)."""
    srv = DashboardServer(snapshot or (lambda: {}), host="127.0.0.1", port=0,
                          dispatcher=dispatcher)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _fetch(port, path):
    url = f"http://127.0.0.1:{port}{path}"
    with urllib.request.urlopen(url, timeout=3) as r:
        return r.status, r.read().decode(), r.headers.get("Content-Type", "")


def _post(port, path, obj):
    """POST JSON and return (status, body, ctype) for both 2xx and error codes."""
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(obj).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status, r.read().decode(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), e.headers.get("Content-Type", "")


class _RecordingController:
    """A fake experiment controller that records dispatched calls."""

    def __init__(self):
        self.calls = []

    def start(self, args):
        self.calls.append(("start", args))
        return {"started": True, "args": args}

    def stop(self, args):
        self.calls.append(("stop", args))
        return {"stopped": True}

    def status(self, args):
        self.calls.append(("status", args))
        return {"phase": "idle"}


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


# --- experiment control downlink (POST /api/experiment, GET /api/experiment/status)

def test_post_experiment_dispatches_valid_opcode():
    ctl = _RecordingController()
    srv = _serve_ctl(Dispatcher({"S": ctl}))
    try:
        status, body, ctype = _post(srv.port, "/api/experiment", {"opcode": "XS1"})
        assert status == 200
        assert "json" in ctype
        payload = json.loads(body)
        assert payload["ok"] is True
        assert payload["result"] == {"started": True, "args": {}}
        assert ctl.calls == [("start", {})]
    finally:
        srv.shutdown()


def test_post_experiment_passes_args_to_controller():
    ctl = _RecordingController()
    srv = _serve_ctl(Dispatcher({"S": ctl}))
    try:
        status, body, _ = _post(srv.port, "/api/experiment",
                                {"opcode": "XS1 chan=9,pcode=10"})
        assert status == 200
        payload = json.loads(body)
        assert payload["ok"] is True
        # args are opaque strings, order preserved, per the opcode grammar
        assert ctl.calls == [("start", {"chan": "9", "pcode": "10"})]
    finally:
        srv.shutdown()


def test_post_experiment_stop_opcode_routes_to_stop():
    ctl = _RecordingController()
    srv = _serve_ctl(Dispatcher({"S": ctl}))
    try:
        status, body, _ = _post(srv.port, "/api/experiment", {"opcode": "XS0"})
        assert status == 200
        assert json.loads(body)["result"] == {"stopped": True}
        assert ctl.calls == [("stop", {})]
    finally:
        srv.shutdown()


def test_post_experiment_malformed_opcode_is_400():
    ctl = _RecordingController()
    srv = _serve_ctl(Dispatcher({"S": ctl}))
    try:
        # 'Q' is not a known experiment letter -> parse_command raises ValueError
        status, body, ctype = _post(srv.port, "/api/experiment", {"opcode": "XQ1"})
        assert status == 400
        assert "json" in ctype
        payload = json.loads(body)
        assert payload["ok"] is False
        assert "error" in payload
        assert ctl.calls == []  # nothing dispatched
    finally:
        srv.shutdown()


def test_post_experiment_missing_opcode_is_400():
    ctl = _RecordingController()
    srv = _serve_ctl(Dispatcher({"S": ctl}))
    try:
        status, body, _ = _post(srv.port, "/api/experiment", {"nope": "XS1"})
        assert status == 400
        payload = json.loads(body)
        assert payload["ok"] is False
        assert ctl.calls == []
    finally:
        srv.shutdown()


def test_post_experiment_without_dispatcher_is_503():
    # a server with no dispatcher configured cannot accept the downlink
    srv = _serve(lambda: {})
    try:
        status, body, _ = _post(srv.port, "/api/experiment", {"opcode": "XS1"})
        assert status == 503
        payload = json.loads(body)
        assert payload["ok"] is False
    finally:
        srv.shutdown()


# --- scanner experiment UI on the served page (bead uwb-qorvo-1hu.6)
# These assert only the page MARKUP + client wiring. The live board-loop
# pause/resume handoff (half-duplex arbitration while the scanner actively
# polls) needs real hardware and is a deliberate follow-up, not tested here.

def test_page_has_scanner_experiment_section():
    srv = _serve(lambda: {})
    try:
        status, body, ctype = _fetch(srv.port, "/")
        assert status == 200
        assert "html" in ctype
        # an experiments section that exposes a Scanner control
        assert 'id="experiments"' in body
        assert "Scanner" in body
        # the Start control sends the scanner start opcode over the downlink
        assert "XS1" in body
    finally:
        srv.shutdown()


def test_page_scanner_posts_and_polls_the_experiment_api():
    srv = _serve(lambda: {})
    try:
        _, body, _ = _fetch(srv.port, "/")
        # start/stop are POSTed to the experiment downlink...
        assert "/api/experiment" in body
        # ...and progress is polled from the status endpoint
        assert "/api/experiment/status" in body
        # must stay self-contained (no external requests) for the con hotspot
        assert "http://" not in body.replace("http://127.0.0.1", "")
    finally:
        srv.shutdown()


# --- transponder experiment UI on the served page (bead uwb-qorvo-1hu.9)
# Mirrors the scanner page assertions above: page MARKUP + client wiring only.
# The live board-loop pause/resume handoff (half-duplex arbitration while the
# transponder actively answers polls) needs real hardware and is a deliberate
# follow-up, NOT tested here.

def test_page_has_transponder_experiment_section():
    srv = _serve(lambda: {})
    try:
        status, body, ctype = _fetch(srv.port, "/")
        assert status == 200
        assert "html" in ctype
        # the experiments section now also exposes a Transponder control
        assert 'id="experiments"' in body
        assert "Transponder" in body
        # the Start control sends the transponder start opcode over the downlink
        assert "XT1" in body
    finally:
        srv.shutdown()


def test_page_transponder_posts_and_polls_the_experiment_api():
    srv = _serve(lambda: {})
    try:
        _, body, _ = _fetch(srv.port, "/")
        # start/stop are POSTed to the experiment downlink...
        assert "/api/experiment" in body
        # ...and progress is polled from the shared status endpoint
        assert "/api/experiment/status" in body
        # must stay self-contained (no external requests) for the con hotspot
        assert "http://" not in body.replace("http://127.0.0.1", "")
    finally:
        srv.shutdown()


def test_experiment_status_reflects_running_experiment():
    ctl = _RecordingController()
    srv = _serve_ctl(Dispatcher({"S": ctl}))
    try:
        # nothing running yet
        status, body, ctype = _fetch(srv.port, "/api/experiment/status")
        assert status == 200
        assert "json" in ctype
        assert json.loads(body)["running"] is None

        # start scanner -> status reflects the running experiment letter
        _post(srv.port, "/api/experiment", {"opcode": "XS1"})
        _, body, _ = _fetch(srv.port, "/api/experiment/status")
        assert json.loads(body)["running"] == "S"

        # stop scanner -> back to nothing running
        _post(srv.port, "/api/experiment", {"opcode": "XS0"})
        _, body, _ = _fetch(srv.port, "/api/experiment/status")
        assert json.loads(body)["running"] is None
    finally:
        srv.shutdown()


# --- mid-experiment board re-enumeration recovery (bead uwb-qorvo-0ux) --------
# When the board USB-disconnects and re-enumerates WHILE an experiment is active
# (arbiter active, mid-pump), a serial exception fires inside board_loop. The
# recovery path must RELEASE the arbiter and clear the quiesce handoff so the
# rebuilt dispatcher and the passive listener start from a clean slate — a stale
# active arbiter would wedge the listener off the port until an explicit stop.
# board_loop is hardware-bound, so we drive it with fake serial/device seams and
# a pump that raises (the re-enumeration), asserting on the arbiter it consults.


class _FakeSerial:
    def setDTR(self, v):
        pass

    def reset_input_buffer(self):
        pass

    def close(self):
        pass


class _FakeDevice:
    """Minimal board double: detects, reports a config, no-ops the listener."""

    def __init__(self, ser):
        pass

    def detect(self):
        return True

    def get_uwbcfg(self):
        return {"CHAN": 9, "TXCODE": 9}

    def stop(self):
        pass

    def start_listener(self):
        pass


def _patch_board_hw(monkeypatch):
    monkeypatch.setattr(sp, "find_cli_port", lambda: "/dev/fake")
    monkeypatch.setattr(sp, "open_cli", lambda port: _FakeSerial())
    monkeypatch.setattr(devmod, "Device", _FakeDevice)


def test_board_loop_releases_arbiter_when_pump_raises_mid_experiment(monkeypatch):
    from uwb_explorer.experiments.arbiter import PortArbiter

    _patch_board_hw(monkeypatch)
    arb = PortArbiter()
    arb.pause()                    # an experiment holds the port when we enter

    stop = threading.Event()

    def boom():
        # the board re-enumerated mid-experiment: the pump hits a serial error.
        # Set stop so the loop exits after ONE recovery instead of reconnecting
        # forever, then raise the way a wedged serial port would.
        stop.set()
        raise OSError("serial disconnected mid-experiment")

    state = DetectorState()
    webmod.board_loop(state, stop, interval=0.0, arbiter=arb, pump=boom)

    # the re-enumeration must leave the arbiter RELEASED and the handoff cleared,
    # so the rebuilt dispatcher and the passive listener start fresh.
    assert arb.is_active() is False
    assert arb.wait_quiesced(0.0) is True
    assert state.snapshot()["status"] == "error"


def test_recover_arbitration_resets_handoff_and_allows_fresh_experiment():
    # After a mid-experiment re-enumeration the arbiter can be left ACTIVE with a
    # half-armed quiesce wait. recover_arbitration must reset it so a subsequent
    # reconnect can start a fresh experiment cleanly.
    from uwb_explorer.experiments.arbiter import PortArbiter, ArbitratedDispatcher

    arb = PortArbiter()
    arb.set_listener_running(True)
    arb.pause()                    # active, and (listener up) quiesce disarmed
    assert arb.is_active() is True
    assert arb.wait_quiesced(0.0) is False   # wedged mid-handoff

    webmod.recover_arbitration(arb)

    assert arb.is_active() is False           # released
    assert arb.wait_quiesced(0.0) is True     # handoff cleared
    # a fresh pause must NOT re-arm the quiesce wait (listener_running reset),
    # i.e. the reconnect starts from a clean slate.
    arb.pause()
    assert arb.wait_quiesced(0.0) is True
    arb.resume()

    # and a brand-new experiment starts + pumps normally on the same arbiter.
    class _Ctrl:
        def __init__(self):
            self.steps = 0

        def start(self, args):
            return {"ok": True}

        def step(self):
            self.steps += 1
            return self.steps < 2

    class _Inner:
        def __init__(self, c):
            self._c = c

        def dispatch(self, cmd):
            return getattr(self._c, cmd.action)(cmd.args)

        def controller_for(self, exp):
            return self._c

    ctrl = _Ctrl()
    wrapped = ArbitratedDispatcher(_Inner(ctrl), arb)
    wrapped.dispatch(parse_command("XS1"))
    assert arb.is_active() is True
    assert wrapped.pump() is True
    assert wrapped.pump() is False            # exhausts and releases cleanly
    assert arb.is_active() is False


def test_recover_arbitration_is_a_noop_when_arbiter_is_none():
    # board_loop may run without arbitration configured; recovery must be safe.
    webmod.recover_arbitration(None)   # must not raise
