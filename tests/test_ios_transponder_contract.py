"""Cross-language contract for the iOS Transponder sub-view (RED phase for 1hu.10).

This MIRRORS the already-shipped Scanner contract in
``tests/test_ios_scanner_contract.py`` (bead 1hu.7). Bead 1hu.4 shipped a
*placeholder* ``TransponderExperimentView`` in
``ios/.../ExperimentsView.swift`` — it just forwards the three BARE opcodes
("XT1"/"XT0"/"XT?") to the shared ``ExperimentDetail`` scaffold, with no way to
choose channels or preamble codes. Bead 1hu.10 turns that placeholder into a
REAL control UI whose START button composes an opcode WITH ARGS in the
``XT1 channels=<list>,pcodes=<list>`` shape, matching the
``TransponderController(args)`` contract from bead 1hu.8 (``transponder.py``
reads ``args["channels"]`` / ``args["pcodes"]`` and ``_parse_csv`` splits each on
both "," and ";" into ints).

There is no Swift compiler on this Pi, so — exactly like
``tests/test_ios_scanner_contract.py`` and ``tests/test_ios_opcode_contract.py``
— this test READS the SwiftUI source as text and pins two things:

  1.  The transponder view is no longer the bare-opcode placeholder: it now
      composes an arg string (``channels=`` / ``pcodes=`` fragments) on top of
      ``ExpOpcode.transponderStart`` and carries interactive selection state.
  2.  The wire string that composition produces AGREES with the shared grammar
      in ``uwb_explorer/experiments/control.py`` — i.e. it PARSES to
      exp ``T`` / action ``start`` with ``channels`` and ``pcodes`` args — so the
      app can't emit a command the Pi dispatcher (or the transponder controller)
      chokes on.

=============================================================================
ARG-FORMAT CONVENTION GREEN MUST EMIT (so iOS <-> Pi stay byte-identical)
=============================================================================
The transponder START command is the bare start opcode + a single space + a CSV
of key=value pairs, keys ``channels`` and ``pcodes``, e.g. built in Swift as:

    ExpOpcode.transponderStart + " channels=" + chansList + ",pcodes=" + pcodesList

A LIST-valued arg crosses the wire with ";" as its sub-delimiter, because
control.py reserves "," for the key=value *pair* separator (comma inside a value
would be misread as a malformed pair). So the DEFAULT command (channels {5,9},
pcodes {9,10,11,12}) is:

        XT1 channels=5;9,pcodes=9;10;11;12

the "," only separates the ``channels=...`` and ``pcodes=...`` pairs, and each
value is a ";"-joined list that ``transponder.TransponderController._parse_csv``
splits on both "," and ";". See the sub-delimiter note in docs/EXPERIMENTS.md
and the shipped Scanner view for the identical encoding.

STOP and STATUS stay the BARE opcodes ``ExpOpcode.transponderStop`` /
``ExpOpcode.transponderStatus`` (no args) — same as every other experiment. The
keys are the plural ``channels`` / ``pcodes`` that ``transponder.py`` reads.

=============================================================================
ACCEPTANCE CHECKLIST FOR TransponderExperimentView (human/validator-reviewed;
SwiftUI is NOT compiled here)
=============================================================================
[ ] Channel selection: toggles/pickers for channel 5 and/or 9 (defaults both
    on, matching transponder.DEFAULT_CHANNELS = (5, 9)).
[ ] Preamble-code selection: toggles/pickers for preamble codes 9-12 (defaults
    all on, matching transponder.DEFAULT_PCODES = (9, 10, 11, 12)).
[ ] Start button calls ``ble.sendExperiment(ExpOpcode.transponderStart +
    " channels=" + <chosen chans ;-joined> + ",pcodes=" + <chosen pcodes
    ;-joined>)`` — args appended to the bare opcode with a leading space, keys
    ``channels``/``pcodes``, list values joined with ";".
[ ] Stop button calls ``ble.sendExperiment(ExpOpcode.transponderStop)``; Status
    button calls ``ble.sendExperiment(ExpOpcode.transponderStatus)`` — bare
    opcodes (no hardcoded "XT1"-style typos).
[ ] A "landmark active" style status indicator IS present, plus an honest
    results area. NOTE: live answered-poll RESULTS require a board/Pi -> BLE
    results uplink that DOES NOT EXIST YET (a future bead). So this view MAY
    show a "answered polls appear here once the board reports them" placeholder,
    or bind to whatever the board already publishes on the existing state
    characteristic. Rendering real answered-poll results is OUT OF SCOPE for
    1hu.10; the deliverable is the CONTROL UI + correctly arg-encoded opcodes.
[ ] ``@EnvironmentObject var ble: BLEManager`` (matches ContentView/HistoryView
    and the shipped ScannerExperimentView style); uses the ``ExpOpcode``
    constants.
[ ] Selection is held in ``@State`` and reflected in the composed command, so
    the emitted list tracks what the user picked.
=============================================================================
"""

from __future__ import annotations

from pathlib import Path

import pytest

from uwb_explorer.experiments.control import (
    ACTIONS,
    EXPERIMENTS,
    parse_command,
)
from uwb_explorer.experiments.transponder import DEFAULT_CHANNELS, DEFAULT_PCODES

# Same real app-target file the 1hu.4 opcode contract and the scanner contract
# already pin.
_REPO_ROOT = Path(__file__).resolve().parent.parent
SWIFT_FILE = (
    _REPO_ROOT
    / "ios"
    / "UWBExplorer"
    / "UWBExplorer"
    / "UWBExplorer"
    / "ExperimentsView.swift"
)

# Every bare opcode the hub already guarantees (4 experiments x 3 actions).
EXPECTED_BARE_OPCODES = {
    "X" + exp + action for exp in EXPERIMENTS for action in ACTIONS
}


def _read_swift() -> str:
    if not SWIFT_FILE.exists():
        pytest.fail(
            "GREEN has not created the iOS Experiments view yet: "
            f"{SWIFT_FILE} does not exist"
        )
    return SWIFT_FILE.read_text(encoding="utf-8")


def _transponder_view_src(text: str) -> str:
    """Slice out just the ``struct TransponderExperimentView`` declaration so the
    presence-asserts target the transponder and can't be satisfied by another
    view (e.g. the already-real ScannerExperimentView).

    Runs from ``struct TransponderExperimentView`` to the next top-level
    ``struct``, ``extension``, or ``#Preview`` (whichever comes first), or end of
    file.
    """
    marker = "struct TransponderExperimentView"
    start = text.find(marker)
    if start == -1:
        pytest.fail(
            "TransponderExperimentView not found in "
            f"{SWIFT_FILE.name}; GREEN must keep the transponder sub-view"
        )
    rest = text[start + len(marker):]
    ends = [
        rest.find("\nstruct "),
        rest.find("\nextension "),
        rest.find("\n#Preview"),
    ]
    ends = [e for e in ends if e != -1]
    stop = min(ends) if ends else len(rest)
    return text[start:start + len(marker) + stop]


# ---------------------------------------------------------------------------
# Guardrail: 1hu.4's 12-bare-opcode guarantee must survive this bead.
# ---------------------------------------------------------------------------

def test_all_twelve_bare_opcodes_still_present():
    """GREEN must not delete or rename the ExpOpcode constants while reworking
    the transponder view — the other three experiments still send bare
    opcodes."""
    text = _read_swift()
    missing = {op for op in EXPECTED_BARE_OPCODES if f'"{op}"' not in text}
    assert not missing, (
        f"{SWIFT_FILE.name} lost bare opcode literals {sorted(missing)}; "
        "keep all 12 (S/T/B/Z x start/stop/status) — see 1hu.4 contract"
    )


# ---------------------------------------------------------------------------
# The transponder view must become a REAL control UI (fails on the placeholder).
# ---------------------------------------------------------------------------

def test_transponder_view_composes_channels_and_pcodes_args():
    """The transponder START must be composed WITH args: the transponder view
    source must contain the ``channels=`` and ``pcodes=`` key fragments. The
    current placeholder forwards only the bare ``ExpOpcode.transponderStart`` and
    so fails this — that absence is the RED signal for 1hu.10."""
    view = _transponder_view_src(_read_swift())
    assert "channels=" in view, (
        "TransponderExperimentView must compose a 'channels=' arg fragment onto "
        "the start opcode; today's placeholder sends the bare opcode only"
    )
    assert "pcodes=" in view, (
        "TransponderExperimentView must compose a 'pcodes=' arg fragment onto "
        "the start opcode; today's placeholder sends the bare opcode only"
    )


def test_transponder_start_args_are_space_separated_from_the_opcode():
    """Per the grammar, args are separated from the opcode by a SINGLE space.
    Pin that the arg list begins with a leading-space `` channels=`` so the
    composed string is ``XT1 channels=...`` and not ``XT1channels=...``."""
    view = _transponder_view_src(_read_swift())
    assert " channels=" in view, (
        "the composed START must put a single space between the opcode and the "
        'args, i.e. contain " channels=" (with the leading space)'
    )


def test_transponder_view_still_uses_the_expopcode_constants():
    """Start builds on ``ExpOpcode.transponderStart``; Stop/Status stay the bare
    ``ExpOpcode.transponderStop`` / ``.transponderStatus`` — no hardcoded opcode
    typos like a literal "XT1"."""
    view = _transponder_view_src(_read_swift())
    for const in (
        "ExpOpcode.transponderStart",
        "ExpOpcode.transponderStop",
        "ExpOpcode.transponderStatus",
    ):
        assert const in view, (
            f"TransponderExperimentView must reference {const} rather than a "
            "hardcoded opcode string"
        )


def test_transponder_view_holds_interactive_selection_state():
    """A real control UI must hold the channel/pcode selection in @State so the
    emitted list tracks what the user picked; the placeholder has none."""
    view = _transponder_view_src(_read_swift())
    assert "@State" in view, (
        "TransponderExperimentView must hold channel/preamble selection in "
        "@State; the placeholder is a static ExperimentDetail with no state"
    )


# ---------------------------------------------------------------------------
# Wire-format agreement: what the app composes must PARSE for the Pi.
# ---------------------------------------------------------------------------

def test_single_value_transponder_start_command_parses_for_the_pi():
    """The convention — bare start opcode + ' channels=<list>,pcodes=<list>' —
    parses via the shared grammar to exp 'T' / action 'start' with both keys.

    Uses single-value selections so it exercises the OPCODE + KEY shape cleanly
    (the multi-value list encoding is pinned separately below)."""
    composed = "XT1" + " channels=" + "5" + ",pcodes=" + "9"
    cmd = parse_command(composed)
    assert cmd.exp == "T"
    assert EXPERIMENTS[cmd.exp] == "transponder"
    assert cmd.action == "start"
    assert cmd.args.get("channels") == "5"
    assert cmd.args.get("pcodes") == "9"


def test_default_transponder_start_command_parses_for_the_pi():
    """The REALISTIC default command (channels 5,9 / pcodes 9,10,11,12) must
    parse via control AND carry the exact list strings ``transponder.py`` splits.

    A LIST-valued arg crosses the wire with ";" as its sub-delimiter, because
    control.py reserves "," for the key=value *pair* separator. So the default
    command is ``XT1 channels=5;9,pcodes=9;10;11;12`` — the "," now only
    separates the ``channels=...`` and ``pcodes=...`` pairs, and each value is a
    ";"-joined list that ``transponder.TransponderController._parse_csv`` splits
    on both "," and ";". See the sub-delimiter note in docs/EXPERIMENTS.md."""
    chans_wire = ";".join(str(c) for c in DEFAULT_CHANNELS)   # "5;9"
    pcodes_wire = ";".join(str(p) for p in DEFAULT_PCODES)    # "9;10;11;12"
    composed = "XT1" + " channels=" + chans_wire + ",pcodes=" + pcodes_wire
    cmd = parse_command(composed)
    assert cmd.exp == "T"
    assert cmd.action == "start"
    # the exact list strings transponder.TransponderController.start() splits.
    assert cmd.args.get("channels") == chans_wire
    assert cmd.args.get("pcodes") == pcodes_wire
