"""High-level control of a DWM3001CDK running the QM33 CLI firmware."""

from __future__ import annotations

import re
from collections.abc import Iterator

from .parser import Event, InfoBlock, parse_line
from .transport import CliSession

_UWBCFG_ORDER = [
    "CHAN", "PLEN", "PAC", "TXCODE", "RXCODE", "SFDTYPE", "DATARATE",
    "PHRMODE", "PHRRATE", "SFDTO", "STSMODE", "STSLEN", "PDOAMODE",
]

_JS_OPEN_RE = re.compile(r"^JS[0-9A-Fa-f]{4}\{")


def _join_js_blocks(lines: list[str]) -> list[str]:
    """Reassemble multi-line JSxxxx{...} blocks into single lines."""
    out: list[str] = []
    buf: str | None = None
    for line in lines:
        if buf is not None:
            buf += line
            if buf.count("{") <= buf.count("}"):
                out.append(buf)
                buf = None
            continue
        if _JS_OPEN_RE.match(line) and line.count("{") > line.count("}"):
            buf = line
            continue
        out.append(line)
    if buf is not None:
        out.append(buf)
    return out


class Device:
    def __init__(self, ser):
        self.session = CliSession(ser)
        self.info: dict = {}
        self.apps: list[str] = []
        self.version: str | None = None
        self.mode: str = "UNKNOWN"

    def _command_blocks(self, cmd: str, timeout: float = 2.0) -> list[Event]:
        # flush so a query issued right after a streaming mode (LISTENER)
        # isn't polluted by leftover frame bytes
        lines = _join_js_blocks(self.session.command(cmd, timeout=timeout, flush=True))
        return [ev for ev in (parse_line(l) for l in lines) if ev is not None]

    def detect(self) -> bool:
        """Stop any running app, query STAT, populate identity fields."""
        self.stop()
        for ev in self._command_blocks("stat"):
            if isinstance(ev, InfoBlock) and "Info" in ev.data:
                self.info = ev.data
                inner = ev.data["Info"]
                self.version = inner.get("Version")
                self.apps = inner.get("Apps", [])
                self.mode = inner.get("Current App", "UNKNOWN")
                return True
        return False

    def stop(self) -> None:
        self.session.send("stop")
        self.mode = "STOP"

    def start_listener(self, full: bool = False) -> None:
        """Start promiscuous sniffing.

        full=True uses 'LISTENER2 1' which dumps the entire captured frame
        (needed to decode MAC headers) at the cost of a 1ms/frame ceiling;
        default is the fast 6-byte-truncated mode.
        """
        base = "listener2" if "LISTENER2" in self.apps else "listener"
        cmd = f"{base} 1" if full and base == "listener2" else base
        self.session.send(cmd)
        self.mode = "LISTENER"

    def start_ranging(self, role: str) -> None:
        assert role in ("initf", "respf")
        self.session.send(role)
        self.mode = role.upper()

    def get_lstat(self) -> dict | None:
        """Read LISTENER PHY event counters (must be in LISTENER mode)."""
        for ev in self._command_blocks("lstat"):
            if isinstance(ev, InfoBlock) and "RX Events" in ev.data:
                return ev.data["RX Events"]
        return None

    def get_uwbcfg(self) -> dict | None:
        for ev in self._command_blocks("uwbcfg"):
            if isinstance(ev, InfoBlock) and "UWB PARAM" in ev.data:
                return ev.data["UWB PARAM"]
        return None

    def set_channel(self, chan: int) -> bool:
        """Rewrite UWBCFG with a new channel, preserving other parameters."""
        params = self.get_uwbcfg()
        if not params:
            return False
        params["CHAN"] = chan
        values = " ".join(str(params[k]) for k in _UWBCFG_ORDER)
        self.session.send(f"uwbcfg {values}")
        return True

    def set_antenna_delay(self, ticks: int) -> None:
        """Program the antenna-delay registers with a calibrated tick value.

        `ticks` is a DW3000 device-time-unit value (~15.65 ps/tick), as
        produced by uwb_explorer.calibration.calibrate(). Antenna delay is
        NOT one of UWBCFG's 13 params (see _UWBCFG_ORDER) — it is a
        separate IDLE-only config command, analogous to TXPOWER/ANTENNA in
        docs/cli-protocol.md §2. Following the standard Qorvo/DecaWave
        convention, the same calibrated value is programmed into both the
        TX and RX antenna-delay registers (see calibration.py's module
        docstring for why), hence `ticks` appears twice on the wire.

        NOTE: the exact `ANTDELAY <tx> <rx>` command name/argument order
        used here was not confirmed against a live board's `HELP` output
        (see bead uwb-qorvo-av8) — verify with `HELP ANTDELAY` on real
        hardware before relying on this in tools/calibrate_antenna_delay.py,
        and adjust the format string below if the firmware differs.
        """
        self.session.send(f"antdelay {ticks} {ticks}")

    def poll_events(self) -> Iterator[Event]:
        """Drain complete lines currently buffered; yield parsed events."""
        pending: list[str] = []
        while True:
            line = self.session.read_line()
            if line is None:
                break
            pending.append(line)
        for joined in _join_js_blocks(pending):
            ev = parse_line(joined)
            if ev is not None:
                yield ev
