"""Serial port discovery for the DWM3001CDK CLI console (J20 native USB)."""

from __future__ import annotations

import os

import serial
from serial.tools import list_ports

# The nRF52833 native USB (J20) enumerates under Nordic's VID.
NORDIC_VID = 0x1915
SEGGER_VID = 0x1366

# Pin a specific CLI console when several boards share the bus (e.g. an
# initiator + a responder): set UWB_CLI_PORT to the exact device path and
# discovery is bypassed in its favour.
CLI_PORT_ENV = "UWB_CLI_PORT"


def find_cli_port() -> str | None:
    """Return the device path most likely to be the CLI console.

    An explicit ``UWB_CLI_PORT`` pin wins (multi-board rigs). Otherwise the
    preference is: a Nordic-VID CDC port (J20) > any non-SEGGER ACM > None.
    The SEGGER J-Link VCOM is explicitly de-prioritised because the QM33
    CLI firmware does NOT expose its console there.
    """
    pinned = os.environ.get(CLI_PORT_ENV, "").strip()
    if pinned:
        return pinned
    ports = list(list_ports.comports())
    for p in ports:
        if p.vid == NORDIC_VID:
            return p.device
    for p in ports:
        if p.vid != SEGGER_VID and p.device and "ACM" in p.device:
            return p.device
    return None


def open_cli(path: str | None = None, baud: int = 115200) -> serial.Serial:
    path = path or find_cli_port()
    if path is None:
        raise RuntimeError(
            "No CLI serial port found. Plug a cable into the board's J20 "
            "(native-USB) micro-USB port — the J-Link port does not carry "
            "the CLI console."
        )
    # Native USB CDC ignores baud, but pyserial still wants a value.
    return serial.Serial(path, baud, timeout=0.1)
