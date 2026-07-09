"""TDD (RED) for serve.build_dispatcher — wiring the REAL scanner onto the web downlink.

bead uwb-qorvo-1hu.6 (Scanner web view). serve.py currently wires a PROVISIONAL
dispatcher of placeholder controllers (``_provisional_dispatcher`` /
``_PlaceholderController``). This bead makes the SCANNER real: a factory
``build_dispatcher(device) -> control.Dispatcher`` that registers a real
``ScannerController(device)`` for letter "S" and keeps placeholder controllers
for T/B/Z until their beads land.

The tested seam is deliberately hardware/thread-free: the factory is driven with
the ScriptedSerial harness (tests.test_device.make_scripted) so the scanner's
active ``initf`` polling runs against canned board replies.

SCOPE GUARD: the live board-loop pause/resume handoff — the half-duplex
arbitration between the scanner's active TX polling and the passive board_loop
that also owns the single serial port — needs real hardware and is verified
out-of-band. It is a deliberate follow-up and is intentionally NOT exercised here.

``build_dispatcher`` does not exist yet; it is imported lazily inside each test so
this module still imports/collects cleanly and each test fails (rather than erroring
the whole file's collection). The failing import IS the RED signal.
"""

from __future__ import annotations

import json

from uwb_explorer.experiments.control import Dispatcher, parse_command
from tests.test_device import UWBCFG_REPLY, make_device, make_scripted


def _build(device):
    # imported here so a missing factory fails the individual test, not collection
    from uwb_explorer.serve import build_dispatcher
    return build_dispatcher(device)


def test_build_dispatcher_returns_a_dispatcher():
    dev, _ = make_device()
    assert isinstance(_build(dev), Dispatcher)


def test_dispatch_xs1_drives_the_real_scanner():
    # the board answers the uwbcfg query so set_uwbcfg can rewrite the PHY combo
    dev, ser = make_scripted({"uwbcfg": UWBCFG_REPLY})
    disp = _build(dev)
    disp.dispatch(parse_command("XS1"))   # start scanner -> drives the first combo
    tx = bytes(ser.tx)
    assert b"uwbcfg" in tx   # PHY reconfigured for the combo
    assert b"initf" in tx    # actively polls that combo


def test_dispatch_xs_status_returns_the_scanner_status_dict():
    dev, _ = make_scripted({"uwbcfg": UWBCFG_REPLY})
    disp = _build(dev)
    disp.dispatch(parse_command("XS1"))
    st = disp.dispatch(parse_command("XS?"))
    for key in ("devices", "total", "step", "running"):
        assert key in st
    assert st["total"] == 8        # default 2 channels x 4 pcodes
    assert st["step"] >= 1         # the first combo has been driven
    assert isinstance(st["devices"], list)
    json.dumps(st)                 # JSON-able for the web status endpoint


def test_scanner_letter_is_the_real_controller_not_a_placeholder():
    # the placeholder's status is {"exp":..., "phase":"unimplemented"}; the real
    # scanner reports concrete sweep progress instead.
    dev, _ = make_scripted({"uwbcfg": UWBCFG_REPLY})
    disp = _build(dev)
    disp.dispatch(parse_command("XS1"))
    st = disp.dispatch(parse_command("XS?"))
    assert "phase" not in st
    assert st["running"] is True


def test_placeholder_letters_still_dispatch_without_crashing():
    # T/B/Z have no real controller yet; the placeholder must answer so the web
    # hub stays fully wired and nothing crashes when those letters are driven.
    dev, _ = make_device()
    disp = _build(dev)
    for letter in ("T", "B", "Z"):
        started = disp.dispatch(parse_command(f"X{letter}1"))
        status = disp.dispatch(parse_command(f"X{letter}?"))
        assert isinstance(started, dict)
        assert isinstance(status, dict)
