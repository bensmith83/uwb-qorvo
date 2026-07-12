"""TDD for the shared experiment control-command convention (RED phase).

The board's 6e5f control characteristic already accepts short string commands
("F1"/"F0" toggle CRC-fail capture, "S<n>" picks an STS mode). This adds a
compact opcode family that drives four experiments over the same channel:

    X<exp><action>[ <args>]

  * "X"      literal prefix marking an experiment opcode
  * <exp>    one letter picking the experiment:
                 S=scanner  T=transponder  B=beacon  Z=fuzzer
  * <action> one char picking the verb:
                 1=start  0=stop  ?=status
  * <args>   optional, separated from the opcode by a single space; a CSV of
             key=value pairs, e.g. "chan=9,pcode=10" or "payload=deadbeef"

Both the Pi dispatcher and the iOS/board sides must agree on this grammar, so
it is pinned here the same way blecodec.KEY_MAP pins the BLE wire format.

The module under test does not exist yet; the failing import IS the RED signal.
GREEN implements `uwb_explorer/experiments/control.py` to satisfy these tests.
"""

from __future__ import annotations

import pytest

from uwb_explorer.experiments.control import (
    EXPERIMENTS,
    ACTIONS,
    ExperimentCommand,
    parse_command,
    format_command,
    Dispatcher,
)


# ---- the pinned convention -------------------------------------------------

def test_convention_maps_letters_and_actions():
    # the four experiments and three verbs are the shared vocabulary
    assert EXPERIMENTS == {
        "S": "scanner",
        "T": "transponder",
        "B": "beacon",
        "Z": "fuzzer",
    }
    assert ACTIONS == {"1": "start", "0": "stop", "?": "status"}


# ---- parsing ---------------------------------------------------------------

@pytest.mark.parametrize(
    "opcode, exp, action",
    [
        ("XS1", "S", "start"),
        ("XS0", "S", "stop"),
        ("XS?", "S", "status"),
        ("XT1", "T", "start"),
        ("XB1", "B", "start"),
        ("XZ1", "Z", "start"),
    ],
)
def test_parse_each_experiment_and_action(opcode, exp, action):
    cmd = parse_command(opcode)
    assert cmd.exp == exp
    assert cmd.action == action
    assert cmd.args == {}


def test_parse_csv_key_value_args():
    cmd = parse_command("XS1 chan=9,pcode=10")
    assert cmd.exp == "S"
    assert cmd.action == "start"
    assert cmd.args == {"chan": "9", "pcode": "10"}


def test_parse_hex_payload_arg():
    cmd = parse_command("XB1 payload=deadbeef")
    assert cmd.exp == "B"
    assert cmd.action == "start"
    assert cmd.args == {"payload": "deadbeef"}


@pytest.mark.parametrize(
    "bad",
    [
        "",            # empty
        "S1",          # missing X prefix
        "XQ1",         # unknown experiment letter
        "XS9",         # unknown action char
        "XS",          # missing action
        "X",           # nothing after prefix
        "XS1chan=9",   # args not separated by a space
    ],
)
def test_unknown_or_malformed_opcode_raises(bad):
    with pytest.raises(ValueError):
        parse_command(bad)


# ---- round-trip ------------------------------------------------------------

@pytest.mark.parametrize(
    "opcode",
    ["XS1", "XT0", "XB?", "XS1 chan=9,pcode=10", "XB1 payload=deadbeef"],
)
def test_round_trip_parse_format_parse(opcode):
    cmd = parse_command(opcode)
    text = format_command(cmd)
    assert parse_command(text) == cmd


def test_format_produces_the_canonical_opcode():
    cmd = ExperimentCommand(exp="S", action="start", args={"chan": "9"})
    assert format_command(cmd) == "XS1 chan=9"
    assert format_command(ExperimentCommand("T", "stop", {})) == "XT0"


# ---- dispatcher ------------------------------------------------------------

class FakeController:
    """Records which verb ran and with what args (stands in for a real one)."""

    def __init__(self):
        self.calls = []

    def start(self, args):
        self.calls.append(("start", args))
        return "started"

    def stop(self, args):
        self.calls.append(("stop", args))
        return "stopped"

    def status(self, args):
        self.calls.append(("status", args))
        return "idle"


def test_dispatcher_routes_start_stop_status_to_the_right_controller():
    scanner, beacon = FakeController(), FakeController()
    disp = Dispatcher({"S": scanner, "B": beacon})

    disp.dispatch(parse_command("XS1 chan=9"))
    disp.dispatch(parse_command("XS0"))
    disp.dispatch(parse_command("XB?"))

    assert scanner.calls == [("start", {"chan": "9"}), ("stop", {})]
    assert beacon.calls == [("status", {})]


def test_dispatcher_returns_controller_result():
    scanner = FakeController()
    disp = Dispatcher({"S": scanner})
    assert disp.dispatch(parse_command("XS1")) == "started"


def test_dispatcher_rejects_unknown_experiment():
    disp = Dispatcher({"S": FakeController()})
    with pytest.raises(ValueError):
        disp.dispatch(parse_command("XT1"))


def test_dispatcher_controller_for_returns_the_registered_controller():
    # the arbiter's pump() needs to reach the controller a start drove to call
    # its step(); controller_for exposes it (None for an unknown letter).
    scanner = FakeController()
    disp = Dispatcher({"S": scanner})
    assert disp.controller_for("S") is scanner
    assert disp.controller_for("Z") is None
