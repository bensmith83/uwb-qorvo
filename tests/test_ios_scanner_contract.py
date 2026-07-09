"""Cross-language contract for the iOS Scanner sub-view (RED phase for 1hu.7).

Bead 1hu.4 shipped a *placeholder* ``ScannerExperimentView`` in
``ios/.../ExperimentsView.swift`` — it just forwards the three BARE opcodes
("XS1"/"XS0"/"XS?") to the shared ``ExperimentDetail`` scaffold, with no way to
choose channels or preamble codes. Bead 1hu.7 turns that placeholder into a
REAL control UI whose START button composes an opcode WITH ARGS in the
``XS1 channels=<csv>,pcodes=<csv>`` shape, matching the
``ScannerController(args)`` contract from bead 1hu.5 (``scanner.py`` reads
``args["channels"]`` / ``args["pcodes"]`` and comma-splits each into ints).

There is no Swift compiler on this Pi, so — exactly like
``tests/test_ios_opcode_contract.py`` — this test READS the SwiftUI source as
text and pins two things:

  1.  The scanner view is no longer the bare-opcode placeholder: it now composes
      an arg string (``channels=`` / ``pcodes=`` fragments) on top of
      ``ExpOpcode.scannerStart`` and carries interactive selection state.
  2.  The wire string that composition produces AGREES with the shared grammar
      in ``uwb_explorer/experiments/control.py`` — i.e. it PARSES to
      exp ``S`` / action ``start`` with ``channels`` and ``pcodes`` args — so the
      app can't emit a command the Pi dispatcher (or the scanner controller)
      chokes on.

=============================================================================
!!! KNOWN CONFLICT THIS RED PHASE SURFACES (read before GREEN) !!!
=============================================================================
The obvious representative command the app will emit for the DEFAULT selection
(channels {5,9}, pcodes {9,10,11,12}) is:

        XS1 channels=5,9,pcodes=9,10,11,12

That string does NOT parse under the current shared grammar:
``control._parse_args`` uses the COMMA as the key=value *pair* separator, so the
bare ``9`` inside ``channels=5,9`` is seen as a malformed pair and
``parse_command`` raises ``ValueError: malformed arg pair: '9'``. Yet
``scanner.ScannerController.start`` REQUIRES ``args["channels"] == "5,9"`` (it
does ``"5,9".split(",")``). So control.py (comma = pair sep) and scanner.py
(comma = list sep INSIDE one value) are mutually incompatible for multi-valued
selections, and no iOS encoding can satisfy both as they stand.

That conflict is the point of ``test_default_scanner_start_command_parses_for_the_pi``
below: it fails RED today and can only go green once the three sides are
reconciled (e.g. list values use a non-comma sub-delimiter shared by
control+scanner+iOS, OR control's arg parser folds bare no-``=`` tokens into the
previous value, OR the app sends one channel+pcode per START). Picking the fix
is a lead/GREEN decision — see the message to "main".

=============================================================================
ARG-FORMAT CONVENTION GREEN MUST EMIT (so iOS ↔ Pi stay byte-identical)
=============================================================================
The scanner START command is the bare start opcode + a single space + a CSV of
key=value pairs, keys ``channels`` and ``pcodes``, e.g. built in Swift as:

    ExpOpcode.scannerStart + " channels=" + chansCSV + ",pcodes=" + pcodesCSV

STOP and STATUS stay the BARE opcodes ``ExpOpcode.scannerStop`` /
``ExpOpcode.scannerStatus`` (no args) — same as every other experiment. The
keys are the plural ``channels`` / ``pcodes`` that ``scanner.py`` reads, NOT the
singular ``chan`` / ``pcode`` shown in the docs' generic example.

=============================================================================
ACCEPTANCE CHECKLIST FOR ScannerExperimentView (human/validator-reviewed;
SwiftUI is NOT compiled here)
=============================================================================
[ ] Channel selection: toggles/pickers for channel 5 and/or 9 (defaults both
    on, matching scanner.DEFAULT_CHANNELS = (5, 9)).
[ ] Preamble-code selection: toggles/pickers for preamble codes 9–12 (defaults
    all on, matching scanner.DEFAULT_PCODES = (9, 10, 11, 12)).
[ ] Start button calls ``ble.sendExperiment(ExpOpcode.scannerStart + " channels="
    + <chosen chans CSV> + ",pcodes=" + <chosen pcodes CSV>)`` — args appended
    to the bare opcode with a leading space, keys ``channels``/``pcodes``.
[ ] Stop button calls ``ble.sendExperiment(ExpOpcode.scannerStop)``; Status
    button calls ``ble.sendExperiment(ExpOpcode.scannerStatus)`` — bare opcodes.
[ ] A results area IS present. NOTE: live discovered-devices results require a
    board/Pi → BLE results uplink that DOES NOT EXIST YET (a future bead). So
    this view MAY show a "results appear here once the board reports them"
    placeholder, or bind to whatever the board already publishes on the existing
    state characteristic. Rendering real scan results is OUT OF SCOPE for 1hu.7;
    the deliverable is the CONTROL UI + correctly arg-encoded opcodes.
[ ] ``@EnvironmentObject var ble: BLEManager`` (matches ContentView/HistoryView
    style); uses the ``ExpOpcode`` constants — no hardcoded "XS1"-style typos.
[ ] Selection is held in ``@State`` and reflected in the composed command, so
    the emitted CSV tracks what the user picked.
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
from uwb_explorer.experiments.scanner import DEFAULT_CHANNELS, DEFAULT_PCODES

# Same real app-target file the 1hu.4 opcode contract already pins.
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


def _scanner_view_src(text: str) -> str:
    """Slice out just the ``struct ScannerExperimentView`` declaration so the
    presence-asserts target the scanner and can't be satisfied by another view.

    Runs from ``struct ScannerExperimentView`` to the next top-level ``struct``,
    ``extension``, or ``#Preview`` (whichever comes first), or end of file.
    """
    marker = "struct ScannerExperimentView"
    start = text.find(marker)
    if start == -1:
        pytest.fail(
            "ScannerExperimentView not found in "
            f"{SWIFT_FILE.name}; GREEN must keep the scanner sub-view"
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
    the scanner view — the other three experiments still send bare opcodes."""
    text = _read_swift()
    missing = {op for op in EXPECTED_BARE_OPCODES if f'"{op}"' not in text}
    assert not missing, (
        f"{SWIFT_FILE.name} lost bare opcode literals {sorted(missing)}; "
        "keep all 12 (S/T/B/Z x start/stop/status) — see 1hu.4 contract"
    )


# ---------------------------------------------------------------------------
# The scanner view must become a REAL control UI (fails on today's placeholder).
# ---------------------------------------------------------------------------

def test_scanner_view_composes_channels_and_pcodes_args():
    """The scanner START must be composed WITH args: the scanner view source
    must contain the ``channels=`` and ``pcodes=`` key fragments. The current
    placeholder forwards only the bare ``ExpOpcode.scannerStart`` and so fails
    this — that absence is the RED signal for 1hu.7."""
    view = _scanner_view_src(_read_swift())
    assert "channels=" in view, (
        "ScannerExperimentView must compose a 'channels=' arg fragment onto the "
        "start opcode; today's placeholder sends the bare opcode only"
    )
    assert "pcodes=" in view, (
        "ScannerExperimentView must compose a 'pcodes=' arg fragment onto the "
        "start opcode; today's placeholder sends the bare opcode only"
    )


def test_scanner_start_args_are_space_separated_from_the_opcode():
    """Per the grammar, args are separated from the opcode by a SINGLE space.
    Pin that the arg CSV begins with a leading-space `` channels=`` so the
    composed string is ``XS1 channels=...`` and not ``XS1channels=...``."""
    view = _scanner_view_src(_read_swift())
    assert " channels=" in view, (
        "the composed START must put a single space between the opcode and the "
        'args, i.e. contain " channels=" (with the leading space)'
    )


def test_scanner_view_still_uses_the_expopcode_constants():
    """Start builds on ``ExpOpcode.scannerStart``; Stop/Status stay the bare
    ``ExpOpcode.scannerStop`` / ``.scannerStatus`` — no hardcoded opcode typos."""
    view = _scanner_view_src(_read_swift())
    for const in (
        "ExpOpcode.scannerStart",
        "ExpOpcode.scannerStop",
        "ExpOpcode.scannerStatus",
    ):
        assert const in view, (
            f"ScannerExperimentView must reference {const} rather than a "
            "hardcoded opcode string"
        )


def test_scanner_view_holds_interactive_selection_state():
    """A real control UI must hold the channel/pcode selection in @State so the
    emitted CSV tracks what the user picked; the placeholder has none."""
    view = _scanner_view_src(_read_swift())
    assert "@State" in view, (
        "ScannerExperimentView must hold channel/preamble selection in @State; "
        "the placeholder is a static ExperimentDetail with no state"
    )


# ---------------------------------------------------------------------------
# Wire-format agreement: what the app composes must PARSE for the Pi.
# ---------------------------------------------------------------------------

def test_single_value_scanner_start_command_parses_for_the_pi():
    """The convention — bare start opcode + ' channels=<csv>,pcodes=<csv>' —
    parses via the shared grammar to exp 'S' / action 'start' with both keys.

    Uses single-value selections so it exercises the OPCODE + KEY shape cleanly
    (the multi-value comma conflict is pinned separately below)."""
    composed = "XS1" + " channels=" + "5" + ",pcodes=" + "9"
    cmd = parse_command(composed)
    assert cmd.exp == "S"
    assert EXPERIMENTS[cmd.exp] == "scanner"
    assert cmd.action == "start"
    assert cmd.args.get("channels") == "5"
    assert cmd.args.get("pcodes") == "9"


def test_default_scanner_start_command_parses_for_the_pi():
    """The REALISTIC default command (channels 5,9 / pcodes 9,10,11,12) must
    parse via control AND carry the exact list strings ``scanner.py`` splits.

    RESOLVED (GREEN/lead decision): a LIST-valued arg crosses the wire with
    ";" as its sub-delimiter, because control.py reserves "," for the
    key=value *pair* separator. So the default command is
    ``XS1 channels=5;9,pcodes=9;10;11;12`` — the "," now only separates the
    ``channels=...`` and ``pcodes=...`` pairs, and each value is a ";"-joined
    list that ``scanner.ScannerController._parse_csv`` splits on both "," and
    ";". See the sub-delimiter note in docs/EXPERIMENTS.md."""
    chans_wire = ";".join(str(c) for c in DEFAULT_CHANNELS)   # "5;9"
    pcodes_wire = ";".join(str(p) for p in DEFAULT_PCODES)    # "9;10;11;12"
    composed = "XS1" + " channels=" + chans_wire + ",pcodes=" + pcodes_wire
    cmd = parse_command(composed)
    assert cmd.exp == "S"
    assert cmd.action == "start"
    # the exact list strings scanner.ScannerController.start() splits into ints
    assert cmd.args.get("channels") == chans_wire
    assert cmd.args.get("pcodes") == pcodes_wire
