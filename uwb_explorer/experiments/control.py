"""Shared opcode grammar for driving the on-board experiments over BLE.

The board's 6e5f control characteristic already accepts short string commands
("F1"/"F0" toggle CRC-fail capture, "S<n>" picks an STS mode). This module pins
a compact opcode family that drives four experiments over the same channel:

    X<exp><action>[ <args>]

  * "X"      literal prefix marking an experiment opcode
  * <exp>    one letter picking the experiment (see EXPERIMENTS):
                 S=scanner  T=transponder  B=beacon  Z=fuzzer
  * <action> one char picking the verb (see ACTIONS):
                 1=start  0=stop  ?=status
  * <args>   optional, separated from the opcode by a single space; a CSV of
             key=value pairs, e.g. "chan=9,pcode=10" or "payload=deadbeef".
             Values stay as opaque strings; argument order is preserved.

Examples: "XS1 chan=9,pcode=10", "XS0", "XB?", "XB1 payload=deadbeef".

The firmware, the Pi dispatcher, and the iOS app must all agree on this grammar,
so it is pinned here the same way blecodec.KEY_MAP pins the BLE wire format.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# single experiment letter -> human name
EXPERIMENTS = {
    "S": "scanner",
    "T": "transponder",
    "B": "beacon",
    "Z": "fuzzer",
}

# single action char -> verb name (also the controller method name)
ACTIONS = {
    "1": "start",
    "0": "stop",
    "?": "status",
}

# verb name -> action char, for formatting back to the wire form
_ACTION_CHARS = {name: char for char, name in ACTIONS.items()}


@dataclass(eq=True)
class ExperimentCommand:
    """A parsed experiment opcode.

    * ``exp``    is the single experiment LETTER, e.g. "S".
    * ``action`` is the verb NAME, e.g. "start"/"stop"/"status".
    * ``args``   is an ordered dict[str, str]; empty when the opcode had none.
    """

    exp: str
    action: str
    args: dict[str, str] = field(default_factory=dict)


def parse_command(s: str) -> ExperimentCommand:
    """Parse an ``X<exp><action>[ <args>]`` opcode into an ExperimentCommand.

    Raises ``ValueError`` on empty, malformed, or unknown opcodes.
    """
    if not s or s[0] != "X":
        raise ValueError(f"not an experiment opcode: {s!r}")
    if len(s) < 3:
        raise ValueError(f"opcode too short: {s!r}")

    exp = s[1]
    if exp not in EXPERIMENTS:
        raise ValueError(f"unknown experiment letter: {exp!r}")

    action_char = s[2]
    if action_char not in ACTIONS:
        raise ValueError(f"unknown action char: {action_char!r}")

    args: dict[str, str] = {}
    if len(s) > 3:
        if s[3] != " ":
            raise ValueError(f"args must be space-separated: {s!r}")
        args = _parse_args(s[4:])

    return ExperimentCommand(exp=exp, action=ACTIONS[action_char], args=args)


def _parse_args(text: str) -> dict[str, str]:
    """Parse a "k=v,k2=v2" CSV of key=value pairs into an ordered dict."""
    args: dict[str, str] = {}
    if text == "":
        return args
    for pair in text.split(","):
        key, sep, value = pair.partition("=")
        if sep != "=" or key == "":
            raise ValueError(f"malformed arg pair: {pair!r}")
        args[key] = value
    return args


def format_command(cmd: ExperimentCommand) -> str:
    """Render an ExperimentCommand back to its canonical opcode string.

    ``parse_command(format_command(cmd)) == cmd`` for any parsed command.
    """
    opcode = "X" + cmd.exp + _ACTION_CHARS[cmd.action]
    if cmd.args:
        csv = ",".join(f"{k}={v}" for k, v in cmd.args.items())
        return f"{opcode} {csv}"
    return opcode


class Dispatcher:
    """Routes parsed commands to per-experiment controller objects.

    ``registry`` maps an experiment LETTER to a controller that duck-types
    ``start(args)`` / ``stop(args)`` / ``status(args)``.
    """

    def __init__(self, registry: dict[str, object]):
        self._registry = registry

    def dispatch(self, cmd: ExperimentCommand):
        """Invoke ``cmd.action`` on the registered controller and return its result."""
        controller = self._registry.get(cmd.exp)
        if controller is None:
            raise ValueError(f"no controller registered for experiment {cmd.exp!r}")
        return getattr(controller, cmd.action)(cmd.args)
