"""CLI serial-port discovery, including the multi-board pin override."""

from __future__ import annotations

from types import SimpleNamespace

from uwb_explorer import serialport


def _fake_ports(monkeypatch, ports):
    monkeypatch.setattr(serialport.list_ports, "comports", lambda: ports)


def test_env_override_pins_a_specific_port(monkeypatch):
    # With several Nordic boards on the bus, UWB_CLI_PORT pins the one this
    # server should own (e.g. the initiator), leaving the others free.
    monkeypatch.setenv("UWB_CLI_PORT", "/dev/serial/by-id/board-A")
    # even if discovery would pick a different Nordic port, the pin wins
    _fake_ports(monkeypatch, [SimpleNamespace(vid=0x1915, device="/dev/ttyACM9")])
    assert serialport.find_cli_port() == "/dev/serial/by-id/board-A"


def test_no_env_prefers_nordic_vid(monkeypatch):
    monkeypatch.delenv("UWB_CLI_PORT", raising=False)
    _fake_ports(monkeypatch, [
        SimpleNamespace(vid=serialport.SEGGER_VID, device="/dev/ttyACM0"),
        SimpleNamespace(vid=serialport.NORDIC_VID, device="/dev/ttyACM1"),
    ])
    assert serialport.find_cli_port() == "/dev/ttyACM1"


def test_empty_env_is_ignored(monkeypatch):
    # a blank pin must not shadow real discovery
    monkeypatch.setenv("UWB_CLI_PORT", "")
    _fake_ports(monkeypatch, [SimpleNamespace(vid=serialport.NORDIC_VID,
                                              device="/dev/ttyACM1")])
    assert serialport.find_cli_port() == "/dev/ttyACM1"
