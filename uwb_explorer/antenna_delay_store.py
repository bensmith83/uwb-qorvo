"""Per-board antenna-delay persistence (bead uwb-qorvo-av8).

**Why:** `tools/calibrate_antenna_delay.py` computes a calibrated
antenna-delay tick value (see uwb_explorer/calibration.py) and applies it to
a board over the wire via `Device.set_antenna_delay()`. That command's `SAVE`
step is meant to persist it into the board's own NVM, but the underlying
`ANTDELAY <tx> <rx>` CLI command is HARDWARE-UNCONFIRMED (see device.py's
caveat) — so this module gives calibration a second, independent, host-side
place to remember the value: a small JSON file keyed by the board's USB
serial number. `uwb_explorer/web.py`'s `board_loop` looks a board up here on
every (re)connect and re-applies its calibrated delay, so a calibration run
survives process restarts/reconnects even if on-board NVM persistence turns
out not to work as expected.

**No hardware here.** Everything in this module is plain JSON-file I/O and
string parsing — no serial I/O, no device object. `path=`/`serial_from_port`
inputs are just strings; nothing here opens a port.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# Default on-disk location: a small per-user JSON cache, analogous to any
# other XDG-ish config file. Callers (board_loop, the calibration tool, and
# every test in this module) can override it via the `path=` argument —
# tests MUST do so (pointing at a tmp_path) rather than touching this.
DEFAULT_STORE_PATH = Path.home() / ".config" / "uwb-explorer" / "antenna_delays.json"

# by-id paths look like:
#   /dev/serial/by-id/usb-<Vendor>_<Product>_<SERIAL>-if00
# The USB serial number is the last underscore-separated field before the
# "-ifNN" interface suffix — stable across replugs/reconnects and unique per
# physical board, unlike a bare /dev/ttyACM0 (which can shift between boards
# on replug/reboot and carries no identity of its own).
_BY_ID_PREFIX = "/dev/serial/by-id/"
_IF_SUFFIX_RE = re.compile(r"-if\d+$")


def serial_from_port(path: str | None) -> str | None:
    """Extract a board's USB serial number from a /dev/serial/by-id/... path.

    Returns None for anything that isn't a by-id path (e.g. a bare
    /dev/ttyACM0 from auto-discovery) or that doesn't match the expected
    "..._<SERIAL>-ifNN" shape — never raises on unexpected input.
    """
    if not path or not path.startswith(_BY_ID_PREFIX):
        return None
    basename = path[len(_BY_ID_PREFIX):]
    stripped = _IF_SUFFIX_RE.sub("", basename)
    if stripped == basename:
        return None  # no -ifNN interface suffix; not the expected shape
    if "_" not in stripped:
        return None  # nothing to split a serial out of
    serial = stripped.rsplit("_", 1)[-1]
    return serial or None


def _read_store(path: Path) -> dict:
    """Load the JSON store as a dict, treating anything unreadable/malformed
    as an empty store rather than raising — this is a best-effort cache."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_delay(serial: str, path: str | Path | None = None) -> int | None:
    """Look up the calibrated antenna-delay ticks stored for `serial`.

    Returns None if there's no stored file, no entry for this serial, the
    file is corrupt, or the stored value isn't an int — robust by design,
    since a bad cache must never be a reason to fail a board connect.
    """
    store_path = Path(path) if path is not None else DEFAULT_STORE_PATH
    value = _read_store(store_path).get(serial)
    return value if isinstance(value, int) else None


def save_delay(serial: str, ticks: int, path: str | Path | None = None) -> None:
    """Persist calibrated `ticks` for `serial`, merging into any existing
    entries for other boards in the same store file."""
    store_path = Path(path) if path is not None else DEFAULT_STORE_PATH
    data = _read_store(store_path)
    data[serial] = int(ticks)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
