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


def test_all_experiment_letters_dispatch_without_crashing():
    # S/T/B/Z are all REAL controllers now (Z became real in bead 1hu.16 — see
    # the fuzzer tests below) — no placeholder letters remain in EXPERIMENTS,
    # so just a smoke check that every letter answers start/status cleanly.
    dev, _ = make_device()
    disp = _build(dev)
    for letter in ("S", "T", "B", "Z"):
        started = disp.dispatch(parse_command(f"X{letter}1"))
        status = disp.dispatch(parse_command(f"X{letter}?"))
        assert isinstance(started, dict) or started is None
        assert isinstance(status, dict)


# --- transponder web view (bead uwb-qorvo-1hu.9) -----------------------------
# Mirrors the scanner wiring above: build_dispatcher must register a REAL
# TransponderController on letter "T" (bead 1hu.8 shipped it), not a placeholder.
# SCOPE GUARD: the live board-loop pause/resume handoff (half-duplex arbitration
# while the transponder actively answers on the shared serial port) needs real
# hardware and is a deliberate follow-up — NOT exercised here.


def test_dispatch_xt1_drives_the_real_transponder():
    # the board answers the uwbcfg query so set_uwbcfg can rewrite the PHY combo
    dev, ser = make_scripted({"uwbcfg": UWBCFG_REPLY})
    disp = _build(dev)
    disp.dispatch(parse_command("XT1"))   # start transponder -> drives first combo
    tx = bytes(ser.tx)
    assert b"uwbcfg" in tx   # PHY reconfigured for the combo
    assert b"respf" in tx    # answers polls on that combo (respf, not initf)


def test_dispatch_xt_status_returns_the_transponder_status_dict():
    dev, _ = make_scripted({"uwbcfg": UWBCFG_REPLY})
    disp = _build(dev)
    disp.dispatch(parse_command("XT1"))
    st = disp.dispatch(parse_command("XT?"))
    for key in ("answered", "total", "step", "running"):
        assert key in st
    assert st["total"] == 8        # default 2 channels x 4 pcodes
    assert st["step"] >= 1         # the first combo has been driven
    assert isinstance(st["answered"], list)
    json.dumps(st)                 # JSON-able for the web status endpoint


def test_transponder_letter_is_the_real_controller_not_a_placeholder():
    # the placeholder's status is {"exp":..., "phase":"unimplemented"}; the real
    # transponder reports concrete answered-poll progress instead. The "answered"
    # key (and the absent "phase") is what distinguishes it from the placeholder.
    dev, _ = make_scripted({"uwbcfg": UWBCFG_REPLY})
    disp = _build(dev)
    disp.dispatch(parse_command("XT1"))
    st = disp.dispatch(parse_command("XT?"))
    assert "phase" not in st
    assert "answered" in st
    assert st["running"] is True


def test_scanner_stays_real_alongside_the_transponder():
    # wiring the real transponder must NOT regress the already-shipped scanner
    # (bead 1hu.6): S still drives a live sweep, T still answers polls.
    dev, _ = make_scripted({"uwbcfg": UWBCFG_REPLY})
    disp = _build(dev)
    disp.dispatch(parse_command("XS1"))
    s_st = disp.dispatch(parse_command("XS?"))
    disp.dispatch(parse_command("XT1"))
    t_st = disp.dispatch(parse_command("XT?"))
    assert "phase" not in s_st and s_st["running"] is True   # scanner still real
    assert "phase" not in t_st and t_st["running"] is True   # transponder real


# --- beacon web view (bead uwb-qorvo-1hu.11) ---------------------------------
# Mirrors the scanner/transponder wiring above: build_dispatcher must register a
# REAL BeaconController on letter "B" (bead 1hu.11 ships it), not a placeholder.
# The beacon is a periodic fixed-frame TX beacon on stock firmware's TCFM — no
# ranging role and no PHY sweep, just one fixed combo — so unlike scanner/
# transponder it has no "total"/"step"/devices-list; "running" plus the
# configured channel/pcode/interval is the whole status shape.
# SCOPE GUARD: the live board-loop pause/resume handoff (half-duplex arbitration
# while the beacon actively transmits on the shared serial port) needs real
# hardware and is a deliberate follow-up — NOT exercised here.


def test_dispatch_xb1_drives_the_real_beacon():
    # the board answers the uwbcfg query so set_uwbcfg can rewrite the PHY combo
    dev, ser = make_scripted({"uwbcfg": UWBCFG_REPLY})
    disp = _build(dev)
    disp.dispatch(parse_command("XB1"))   # start beacon -> configures + fires tcfm
    tx = bytes(ser.tx)
    assert b"uwbcfg" in tx   # PHY reconfigured for the beacon's combo
    assert b"tcfm" in tx     # begins the periodic fixed-frame TX beacon


def test_dispatch_xb_status_returns_the_beacon_status_dict():
    dev, _ = make_scripted({"uwbcfg": UWBCFG_REPLY})
    disp = _build(dev)
    disp.dispatch(parse_command("XB1"))
    st = disp.dispatch(parse_command("XB?"))
    for key in ("running", "channel", "pcode", "interval"):
        assert key in st
    assert st["running"] is True
    json.dumps(st)   # JSON-able for the web status endpoint


def test_beacon_letter_is_the_real_controller_not_a_placeholder():
    # the placeholder's status is {"exp":..., "phase":"unimplemented"}; the real
    # beacon reports concrete channel/pcode/interval config instead.
    dev, _ = make_scripted({"uwbcfg": UWBCFG_REPLY})
    disp = _build(dev)
    disp.dispatch(parse_command("XB1"))
    st = disp.dispatch(parse_command("XB?"))
    assert "phase" not in st
    assert "channel" in st
    assert st["running"] is True


def test_dispatch_xb1_honors_channel_pcode_and_interval_args():
    dev, ser = make_scripted({"uwbcfg": UWBCFG_REPLY})
    disp = _build(dev)
    disp.dispatch(parse_command("XB1 channel=9,pcode=12,interval=2.5"))
    st = disp.dispatch(parse_command("XB?"))
    assert st["channel"] == 9
    assert st["pcode"] == 12
    assert st["interval"] == 2.5
    assert b"uwbcfg 9 " in bytes(ser.tx)


def test_dispatch_xb0_stops_the_beacon():
    dev, ser = make_scripted({"uwbcfg": UWBCFG_REPLY})
    disp = _build(dev)
    disp.dispatch(parse_command("XB1"))
    disp.dispatch(parse_command("XB0"))
    assert b"stop\r\n" in bytes(ser.tx)
    st = disp.dispatch(parse_command("XB?"))
    assert st["running"] is False


def test_scanner_and_transponder_stay_real_alongside_the_beacon():
    # wiring the real beacon must NOT regress the already-shipped scanner/
    # transponder: S still sweeps, T still answers, B now really beacons too.
    dev, _ = make_scripted({"uwbcfg": UWBCFG_REPLY})
    disp = _build(dev)
    disp.dispatch(parse_command("XS1"))
    s_st = disp.dispatch(parse_command("XS?"))
    disp.dispatch(parse_command("XT1"))
    t_st = disp.dispatch(parse_command("XT?"))
    disp.dispatch(parse_command("XB1"))
    b_st = disp.dispatch(parse_command("XB?"))
    assert "phase" not in s_st and s_st["running"] is True   # scanner still real
    assert "phase" not in t_st and t_st["running"] is True   # transponder real
    assert "phase" not in b_st and b_st["running"] is True   # beacon now real


# --- fuzzer web view (bead uwb-qorvo-1hu.16) ----------------------------------
# Mirrors the scanner/transponder/beacon wiring above: build_dispatcher must
# register a REAL FuzzerController on letter "Z" (bead 1hu.16 ships it), not a
# placeholder. AUTHORIZED SECURITY-RESEARCH TOOLING: fires ONE malformed frame
# per deliberate start(), then switches to LISTENER to capture any reaction —
# no PHY sweep, no "total"/"step"; "running"/"last_case"/"reactions" is the
# whole status shape (see uwb_explorer/experiments/fuzzer.py).
# SCOPE GUARD: the live board-loop pause/resume handoff (half-duplex
# arbitration while the fuzzer fires + listens on the shared serial port)
# needs real hardware and is a deliberate follow-up — NOT exercised here.
# The .15 firmware's actual malformed-frame TX is deferred to hardware too —
# here we only assert the right `fuzztx <id>` wire command is sent.


def test_dispatch_xz1_drives_the_real_fuzzer():
    dev, ser = make_device()
    disp = _build(dev)
    disp.dispatch(parse_command("XZ1"))   # fire the default case (bad-crc)
    tx = bytes(ser.tx)
    assert b"fuzztx 0\r\n" in tx    # the fixed catalog's default case id
    assert b"listener\r\n" in tx    # switches to LISTENER to capture reactions


def test_dispatch_xz_status_returns_the_fuzzer_status_dict():
    dev, _ = make_device()
    disp = _build(dev)
    disp.dispatch(parse_command("XZ1"))
    st = disp.dispatch(parse_command("XZ?"))
    for key in ("running", "last_case", "reactions"):
        assert key in st
    assert st["running"] is True
    assert st["last_case"] == "bad-crc"
    assert isinstance(st["reactions"], list)
    json.dumps(st)   # JSON-able for the web status endpoint


def test_fuzzer_letter_is_the_real_controller_not_a_placeholder():
    # the placeholder's status is {"exp":..., "phase":"unimplemented"}; the
    # real fuzzer reports concrete case/reactions state instead.
    dev, _ = make_device()
    disp = _build(dev)
    disp.dispatch(parse_command("XZ1"))
    st = disp.dispatch(parse_command("XZ?"))
    assert "phase" not in st
    assert "last_case" in st
    assert st["running"] is True


def test_dispatch_xz1_honors_the_case_arg():
    dev, ser = make_device()
    disp = _build(dev)
    disp.dispatch(parse_command("XZ1 case=illegal-sts"))
    st = disp.dispatch(parse_command("XZ?"))
    assert st["last_case"] == "illegal-sts"
    assert b"fuzztx 4\r\n" in bytes(ser.tx)


def test_dispatch_xz0_stops_the_fuzzer_and_restores_idle():
    dev, ser = make_device()
    disp = _build(dev)
    disp.dispatch(parse_command("XZ1"))
    disp.dispatch(parse_command("XZ0"))
    assert b"stop\r\n" in bytes(ser.tx)
    st = disp.dispatch(parse_command("XZ?"))
    assert st["running"] is False


def test_scanner_transponder_and_beacon_stay_real_alongside_the_fuzzer():
    # wiring the real fuzzer must NOT regress the already-shipped scanner/
    # transponder/beacon: S still sweeps, T still answers, B still beacons,
    # Z now really fires a malformed frame too.
    dev, ser = make_scripted({"uwbcfg": UWBCFG_REPLY})
    disp = _build(dev)
    disp.dispatch(parse_command("XS1"))
    s_st = disp.dispatch(parse_command("XS?"))
    disp.dispatch(parse_command("XT1"))
    t_st = disp.dispatch(parse_command("XT?"))
    disp.dispatch(parse_command("XB1"))
    b_st = disp.dispatch(parse_command("XB?"))
    disp.dispatch(parse_command("XZ1"))
    z_st = disp.dispatch(parse_command("XZ?"))
    assert "phase" not in s_st and s_st["running"] is True   # scanner still real
    assert "phase" not in t_st and t_st["running"] is True   # transponder real
    assert "phase" not in b_st and b_st["running"] is True   # beacon still real
    assert "phase" not in z_st and z_st["running"] is True   # fuzzer now real
