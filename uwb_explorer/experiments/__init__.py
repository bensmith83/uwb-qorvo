"""Experiment control plane for the UWB Explorer.

See :mod:`uwb_explorer.experiments.control` for the shared opcode grammar that
the board firmware, the Pi dispatcher, and the iOS app all agree on.
"""

from uwb_explorer.experiments.control import (
    ACTIONS,
    EXPERIMENTS,
    Dispatcher,
    ExperimentCommand,
    format_command,
    parse_command,
)

__all__ = [
    "ACTIONS",
    "EXPERIMENTS",
    "Dispatcher",
    "ExperimentCommand",
    "format_command",
    "parse_command",
]
