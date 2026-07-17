"""Per-board antenna-delay persistence (bead uwb-qorvo-av8).

Pure/hardware-free: no serial I/O, only JSON-file I/O keyed by a board's USB
serial number, plus a parser that pulls that serial out of a
/dev/serial/by-id/... port path. See uwb_explorer/web.py's board_loop for
where this auto-applies a stored delay on (re)connect, and
tools/calibrate_antenna_delay.py for where a freshly-computed delay gets
saved here.

Every test that touches the filesystem passes an explicit tmp_path-based
`path=` — never the real default (a real home dir), per the module's
"path injectable/overridable for tests" contract.
"""

from __future__ import annotations

from uwb_explorer.antenna_delay_store import (
    DEFAULT_STORE_PATH,
    load_delay,
    save_delay,
    serial_from_port,
)


# --- save_delay / load_delay round trip ------------------------------------

def test_save_then_load_round_trip(tmp_path):
    path = tmp_path / "antenna_delays.json"
    save_delay("ABC123", 16449, path=path)
    assert load_delay("ABC123", path=path) == 16449


def test_load_missing_file_returns_none(tmp_path):
    path = tmp_path / "does-not-exist.json"
    assert load_delay("ABC123", path=path) is None


def test_load_corrupt_json_returns_none(tmp_path):
    path = tmp_path / "antenna_delays.json"
    path.write_text("{not valid json")
    assert load_delay("ABC123", path=path) is None


def test_load_unknown_serial_returns_none(tmp_path):
    path = tmp_path / "antenna_delays.json"
    save_delay("ABC123", 16449, path=path)
    assert load_delay("SOME-OTHER-SERIAL", path=path) is None


def test_save_preserves_other_serials(tmp_path):
    path = tmp_path / "antenna_delays.json"
    save_delay("BOARD-A", 16400, path=path)
    save_delay("BOARD-B", 16500, path=path)
    assert load_delay("BOARD-A", path=path) == 16400
    assert load_delay("BOARD-B", path=path) == 16500


def test_save_overwrites_same_serial(tmp_path):
    path = tmp_path / "antenna_delays.json"
    save_delay("BOARD-A", 16400, path=path)
    save_delay("BOARD-A", 16420, path=path)
    assert load_delay("BOARD-A", path=path) == 16420


def test_save_creates_parent_directories(tmp_path):
    path = tmp_path / "nested" / "config" / "antenna_delays.json"
    save_delay("BOARD-A", 16400, path=path)
    assert load_delay("BOARD-A", path=path) == 16400


def test_load_survives_file_that_is_not_a_json_object(tmp_path):
    # e.g. a top-level JSON list instead of the expected {serial: ticks} dict
    path = tmp_path / "antenna_delays.json"
    path.write_text("[1, 2, 3]")
    assert load_delay("ABC123", path=path) is None


def test_default_store_path_is_under_a_config_dir():
    # sanity check on the constant only — never actually written to in tests
    assert str(DEFAULT_STORE_PATH).endswith("uwb-explorer/antenna_delays.json")


# --- serial_from_port --------------------------------------------------

def test_serial_from_port_extracts_serial_from_by_id_path():
    path = "/dev/serial/by-id/usb-Nordic_Semiconductor_DWM3001CDK_ABC123456-if00"
    assert serial_from_port(path) == "ABC123456"


def test_serial_from_port_handles_a_different_interface_number():
    path = "/dev/serial/by-id/usb-Nordic_Semiconductor_DWM3001CDK_XYZ999-if02"
    assert serial_from_port(path) == "XYZ999"


def test_serial_from_port_returns_none_for_bare_device_path():
    assert serial_from_port("/dev/ttyACM0") is None


def test_serial_from_port_returns_none_for_none():
    assert serial_from_port(None) is None


def test_serial_from_port_returns_none_for_empty_string():
    assert serial_from_port("") is None


def test_serial_from_port_returns_none_when_missing_if_suffix():
    # under the by-id dir, but doesn't end in the expected -ifNN suffix
    assert serial_from_port("/dev/serial/by-id/usb-no-interface-suffix") is None


def test_serial_from_port_returns_none_when_no_underscore_before_suffix():
    # nothing to split a serial out of before the -ifNN suffix
    assert serial_from_port("/dev/serial/by-id/usb-noUnderscoreHere-if00") is None
