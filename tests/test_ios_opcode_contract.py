"""Cross-language contract test: the iOS Experiments hub must speak the same
`X<exp><action>` opcode grammar the Pi dispatcher parses (RED phase for 1hu.4).

There is no Swift compiler on this Pi, so instead of building the iOS app this
test READS the SwiftUI source as text and checks that every experiment-control
opcode string literal the app will send is a valid opcode under the shared
grammar pinned in `uwb_explorer/experiments/control.py` (docs/EXPERIMENTS.md).
That is the guarantee we actually care about: iOS and the Pi/firmware agree on
the wire strings.

=============================================================================
OPCODE-LITERAL CONVENTION THAT GREEN MUST EMIT
=============================================================================
GREEN creates `ios/UWBExplorer/UWBExplorer/UWBExplorer/ExperimentsView.swift`.
Somewhere in that file the four experiments' control opcodes must appear as
plain double-quoted Swift string literals, each EXACTLY the 3-char opcode
"X<exp><action>" (no args) — e.g. defined as Swift constants:

    enum ExpOpcode {
        static let scannerStart  = "XS1"   // start
        static let scannerStop   = "XS0"   // stop
        static let scannerStatus = "XS?"   // status
        // ... transponder (T), beacon (B), fuzzer (Z) likewise
    }

The full set of 12 literals that MUST be present (4 experiments x 3 actions):

        start   stop    status
    S   "XS1"   "XS0"   "XS?"     scanner
    T   "XT1"   "XT0"   "XT?"     transponder
    B   "XB1"   "XB0"   "XB?"     beacon
    Z   "XZ1"   "XZ0"   "XZ?"     fuzzer

exp letters {S,T,B,Z} and action chars {1=start,0=stop,?=status} come straight
from control.EXPERIMENTS / control.ACTIONS — the test derives the expected set
from those tables, so if the grammar changes in one place this test moves with
it. The literals must be the bare opcode ("XS1"); args (" chan=9,...") are built
at runtime and are out of scope for this contract.

=============================================================================
ACCEPTANCE CHECKLIST FOR THE SWIFTUI STRUCTURE (human/validator-reviewed;
not compiled here)
=============================================================================
[ ] A 4th tab "Experiments" is added to the TabView in UWBExplorerApp.swift,
    e.g. `.tabItem { Label("Experiments", systemImage: "flask") }` (or
    "bolt.horizontal"), pointing at `ExperimentsView()`.
[ ] ExperimentsView is a NavigationStack containing a List with NavigationLinks
    to 4 sub-screens — Scanner, Transponder, Beacon, Fuzzer. Placeholder
    sub-views are acceptable for this bead.
[ ] BLEManager gains a PUBLIC method to send an experiment opcode that reuses
    the existing private `writeCtrl(_:)` path (which does
    `p.writeValue(Data(cmd.utf8), for: ctrl, type: .withoutResponse)`), e.g.
    `func sendExperiment(_ opcode: String) { writeCtrl(opcode) }`.
[ ] ExperimentsView (and its sub-views) reach the manager via
    `@EnvironmentObject var ble: BLEManager`, matching ContentView/HistoryView
    style; no new BLE plumbing.
=============================================================================
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from uwb_explorer.experiments.control import (
    ACTIONS,
    EXPERIMENTS,
    parse_command,
)

# The REAL app target's view file GREEN will create. The app entry TabView is
# in the sibling UWBExplorerApp.swift.
_REPO_ROOT = Path(__file__).resolve().parent.parent
SWIFT_FILE = (
    _REPO_ROOT
    / "ios"
    / "UWBExplorer"
    / "UWBExplorer"
    / "UWBExplorer"
    / "ExperimentsView.swift"
)

# A double-quoted 3-char opcode literal: "X" + exp letter + action char.
_OPCODE_LITERAL = re.compile(r'"(X[A-Za-z][0-9?])"')

# Every opcode iOS must be able to send: 4 experiments x 3 actions = 12.
EXPECTED_OPCODES = {
    "X" + exp + action for exp in EXPERIMENTS for action in ACTIONS
}


def _read_swift() -> str:
    """Return the ExperimentsView.swift text, or fail cleanly if GREEN hasn't
    created it yet (this is the RED signal for this bead)."""
    if not SWIFT_FILE.exists():
        pytest.fail(
            "GREEN has not created the iOS Experiments view yet: "
            f"{SWIFT_FILE} does not exist"
        )
    return SWIFT_FILE.read_text(encoding="utf-8")


def _opcode_literals(text: str) -> set[str]:
    """Every distinct 3-char opcode string literal found in the Swift source."""
    return set(_OPCODE_LITERAL.findall(text))


def test_experiments_view_file_exists():
    """GREEN must add the Experiments view to the real app target."""
    assert SWIFT_FILE.exists(), (
        f"expected GREEN to create {SWIFT_FILE}"
    )


def test_every_opcode_literal_the_app_sends_parses():
    """Every opcode literal in the Swift source must be a valid opcode whose
    parsed exp/action match the shared grammar — no drift between the app and
    the Pi dispatcher."""
    text = _read_swift()
    literals = _opcode_literals(text)
    assert literals, (
        f"found no \"X<exp><action>\" opcode literals in {SWIFT_FILE.name}; "
        "GREEN must emit them (see convention at top of this file)"
    )
    for op in sorted(literals):
        cmd = parse_command(op)  # raises ValueError on anything malformed
        assert op[1] in EXPERIMENTS, f"{op!r}: unknown experiment letter"
        assert cmd.exp == op[1]
        assert cmd.action == ACTIONS[op[2]]
        assert cmd.args == {}


def test_all_four_experiments_times_three_actions_are_present():
    """Coverage: all 12 opcodes (S/T/B/Z x start/stop/status) must appear, so
    GREEN can't silently omit an experiment or a verb."""
    text = _read_swift()
    literals = _opcode_literals(text)
    missing = EXPECTED_OPCODES - literals
    assert not missing, (
        f"{SWIFT_FILE.name} is missing opcode literals {sorted(missing)}; "
        f"expected all 12 of {sorted(EXPECTED_OPCODES)}"
    )
