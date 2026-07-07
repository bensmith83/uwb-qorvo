"""Line-oriented session over a pyserial-compatible object."""

from __future__ import annotations

import re
import time

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class CliSession:
    """Wraps a serial-like object with line framing and command helpers.

    The serial object only needs read(size), write(bytes) and in_waiting.
    Partial lines are buffered across read_line() calls so a timeout never
    drops bytes.
    """

    def __init__(self, ser, *, quiet_time: float = 0.3):
        self._ser = ser
        self._buf = bytearray()
        self._quiet_time = quiet_time

    def send(self, cmd: str) -> None:
        self._ser.write(cmd.encode("ascii") + b"\r\n")

    def _pop_line(self) -> str | None:
        m = re.search(rb"\r\n|\n|\r", self._buf)
        if not m:
            return None
        raw = bytes(self._buf[: m.start()])
        del self._buf[: m.end()]
        text = raw.decode("utf-8", errors="replace")
        return _ANSI_RE.sub("", text)

    def read_line(self, *, skip_blank: bool = False, deadline: float | None = None) -> str | None:
        """Return the next complete line (terminator stripped), or None on timeout."""
        while True:
            line = self._pop_line()
            if line is not None:
                if skip_blank and line.strip() == "":
                    continue
                return line
            waiting = getattr(self._ser, "in_waiting", 0)
            chunk = self._ser.read(max(1, waiting))
            if not chunk:
                if deadline is not None and time.monotonic() < deadline:
                    continue
                return None
            self._buf.extend(chunk)

    def flush_input(self) -> None:
        """Discard any buffered/pending received bytes."""
        self._buf.clear()
        # drain whatever the OS/underlying object still holds
        while getattr(self._ser, "in_waiting", 0):
            if not self._ser.read(self._ser.in_waiting):
                break
        reset = getattr(self._ser, "reset_input_buffer", None)
        if callable(reset):
            reset()

    def command(self, cmd: str, *, timeout: float = 2.0, flush: bool = False) -> list[str]:
        """Send a command, collect response lines until the port goes quiet.

        With flush=True, stale received bytes are discarded first so the
        reply can't be contaminated by a previously-streaming mode.
        """
        if flush:
            self.flush_input()
        self.send(cmd)
        lines: list[str] = []
        deadline = time.monotonic() + timeout
        while True:
            line = self.read_line(deadline=deadline if not lines else None)
            if line is None:
                break
            lines.append(line)
        return lines
