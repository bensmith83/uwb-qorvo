"""Transport layer: line-oriented session over a serial-like object.

The DWM3001CDK CLI firmware talks a line-based UART protocol. CliSession
wraps a pyserial-compatible object (duck-typed: read/write/in_waiting/timeout)
so everything is testable with a fake.
"""

import pytest

from uwb_explorer.transport import CliSession


class FakeSerial:
    """Minimal pyserial stand-in: canned rx bytes, records tx writes."""

    def __init__(self, rx: bytes = b""):
        self._rx = bytearray(rx)
        self.tx = bytearray()
        self.timeout = 0.1

    def read(self, size: int = 1) -> bytes:
        chunk = bytes(self._rx[:size])
        del self._rx[:size]
        return chunk  # empty when exhausted, like a timed-out serial read

    def write(self, data: bytes) -> int:
        self.tx.extend(data)
        return len(data)

    @property
    def in_waiting(self) -> int:
        return len(self._rx)

    def feed(self, data: bytes) -> None:
        self._rx.extend(data)


def test_send_command_appends_crlf_and_writes_bytes():
    ser = FakeSerial()
    sess = CliSession(ser)
    sess.send("STAT")
    assert bytes(ser.tx) == b"STAT\r\n"


def test_read_line_returns_decoded_line_without_terminator():
    ser = FakeSerial(rx=b"hello world\r\n")
    sess = CliSession(ser)
    assert sess.read_line() == "hello world"


def test_read_line_handles_bare_lf():
    ser = FakeSerial(rx=b"line1\nline2\n")
    sess = CliSession(ser)
    assert sess.read_line() == "line1"
    assert sess.read_line() == "line2"


def test_read_line_returns_none_on_timeout():
    ser = FakeSerial(rx=b"incomplete, no terminator")
    sess = CliSession(ser)
    assert sess.read_line() is None
    # partial data must not be lost: completing the line later works
    ser.feed(b" ...done\r\n")
    assert sess.read_line() == "incomplete, no terminator ...done"


def test_read_line_strips_ansi_and_prompt_noise():
    # CLI firmware echoes prompts/colour codes; parser wants clean text
    ser = FakeSerial(rx=b"\x1b[32mok\x1b[0m\r\n")
    sess = CliSession(ser)
    assert sess.read_line() == "ok"


def test_read_line_skips_blank_lines_when_requested():
    ser = FakeSerial(rx=b"\r\n\r\nreal\r\n")
    sess = CliSession(ser)
    assert sess.read_line(skip_blank=True) == "real"


def test_command_roundtrip_collects_lines_until_quiet():
    ser = FakeSerial(rx=b"resp line 1\r\nresp line 2\r\n")
    sess = CliSession(ser)
    lines = sess.command("STAT")
    assert bytes(ser.tx) == b"STAT\r\n"
    assert lines == ["resp line 1", "resp line 2"]


def test_command_flushes_stale_rx_before_sending():
    # Leftover bytes from a previous streaming mode must not appear in the
    # response to a fresh command. The genuine reply only arrives AFTER the
    # command is written, so model that with a write-triggered feed.
    class ReplyOnWrite(FakeSerial):
        def write(self, data):
            n = super().write(data)
            if data.strip():  # the command itself triggers the device reply
                self.feed(b"CHAN:9\r\n")
            return n

    ser = ReplyOnWrite(rx=b"stale listener frame junk\r\n")
    sess = CliSession(ser)
    lines = sess.command("uwbcfg", flush=True)
    assert "stale listener frame junk" not in lines
    assert lines == ["CHAN:9"]
