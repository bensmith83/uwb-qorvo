"""Device driver: firmware detection and app control over a CliSession."""

from tests.test_transport import FakeSerial
from uwb_explorer.device import Device

STAT_REPLY = (
    b'JS010F{"Info":{\r\n'
    b'"Device":"DWM3001CDK - DW3_QM33_SDK - FreeRTOS",\r\n'
    b'"Current App":"STOP",\r\n'
    b'"Version":"0.1.1-221028",\r\n'
    b'"Build":"Oct 28 2022",\r\n'
    b'"Apps":["LISTENER2","TCFM","TCWM","RESPF","INITF"],\r\n'
    b'"Driver":"DW3XXX Device Driver Version 06.00.14",\r\n'
    b'"UWB stack":"R11.9.2"}}\r\n'
    b"ok\r\n"
)


class ScriptedSerial(FakeSerial):
    """Feeds a canned reply when a matching command is written.

    Models real hardware: the reply arrives AFTER the command is sent, so it
    survives Device's flush-before-query. `rules` maps a command substring to
    the bytes the device emits in response.
    """

    def __init__(self, rules: dict[str, bytes] | None = None):
        super().__init__()
        self._rules = rules or {}

    def write(self, data):
        n = super().write(data)
        cmd = data.decode("ascii", "ignore").strip().lower()
        if cmd:
            for match, reply in self._rules.items():
                if cmd.split()[0] == match:
                    self.feed(reply)
                    break
        return n


def make_device(rx: bytes = b"") -> tuple[Device, FakeSerial]:
    ser = FakeSerial(rx=rx)
    return Device(ser), ser


def make_scripted(rules: dict[str, bytes]) -> tuple[Device, ScriptedSerial]:
    ser = ScriptedSerial(rules)
    return Device(ser), ser


def test_detect_populates_info_and_apps():
    dev, ser = make_scripted({"stat": STAT_REPLY})
    assert dev.detect()
    assert dev.version == "0.1.1-221028"
    assert "LISTENER2" in dev.apps
    assert b"stop\r\n" in bytes(ser.tx)  # ensure idle before querying
    assert b"stat\r\n" in bytes(ser.tx)


def test_detect_joins_multiline_js_blocks():
    # the Info block spans lines; detect() must reassemble it
    dev, _ = make_scripted({"stat": STAT_REPLY})
    dev.detect()
    assert dev.info["Info"]["UWB stack"] == "R11.9.2"


def test_start_listener_uses_listener2_when_available():
    dev, ser = make_scripted({"stat": STAT_REPLY})
    dev.detect()
    dev.start_listener()
    assert b"listener2\r\n" in bytes(ser.tx)


def test_start_listener_falls_back_to_listener():
    reply = STAT_REPLY.replace(b'"LISTENER2",', b'"LISTENER",')
    dev, ser = make_scripted({"stat": reply})
    dev.detect()
    dev.start_listener()
    assert b"listener\r\n" in bytes(ser.tx)
    assert b"listener2" not in bytes(ser.tx)


def test_stop_sends_stop():
    dev, ser = make_device()
    dev.stop()
    assert bytes(ser.tx) == b"stop\r\n"


def test_start_ranging_responder_and_initiator():
    dev, ser = make_device()
    dev.start_ranging("respf")
    dev.start_ranging("initf")
    assert b"respf\r\n" in bytes(ser.tx)
    assert b"initf\r\n" in bytes(ser.tx)


def test_set_channel_rewrites_uwbcfg_preserving_other_params():
    uwbcfg_reply = (
        b'JS00BE{"UWB PARAM":{\r\n"CHAN":9,\r\n"PLEN":64,\r\n"PAC":8,\r\n'
        b'"TXCODE":9,\r\n"RXCODE":9,\r\n"SFDTYPE":3,\r\n"DATARATE":6810,\r\n'
        b'"PHRMODE":0,\r\n"PHRRATE":0,\r\n"SFDTO":65,\r\n"STSMODE":0,\r\n'
        b'"STSLEN":64,\r\n"PDOAMODE":1}}\r\nok\r\n'
    )
    dev, ser = make_scripted({"uwbcfg": uwbcfg_reply})
    dev.set_channel(5)
    assert b"uwbcfg 5 64 8 9 9 3 6810 0 0 65 0 64 1\r\n" in bytes(ser.tx)


def test_poll_events_yields_parsed_events():
    dev, ser = make_device()
    ser.feed(b'{"Block":1, "results":[{"Addr":"0x0001","Status":"Ok","D_cm":42}]}\r\n')
    ser.feed(b'JS00EF{"LSTN":[49,2B],"TS":"0x1","O":2}\r\n')
    events = list(dev.poll_events())
    assert len(events) == 2
    assert events[0].results[0].distance_cm == 42
    assert events[1].payload == b"\x49\x2b"


def test_start_listener_full_dump_uses_listener2_1():
    dev, ser = make_scripted({"stat": STAT_REPLY})
    dev.detect()
    dev.start_listener(full=True)
    assert b"listener2 1\r\n" in bytes(ser.tx)


def test_start_listener_default_is_fast_mode():
    dev, ser = make_scripted({"stat": STAT_REPLY})
    dev.detect()
    dev.start_listener()
    assert b"listener2\r\n" in bytes(ser.tx)
    assert b"listener2 1" not in bytes(ser.tx)


LSTAT_REPLY = (
    b'JS0085{"RX Events":{\r\n"CRCG":3,\r\n"CRCB":7,\r\n"ARFE":0,\r\n'
    b'"PHE":5,\r\n"RSL":0,\r\n"SFDTO":2,\r\n"PTO":1,\r\n"FTO":0,\r\n'
    b'"STSE":0,\r\n"STSG":0,\r\n"SFDD":12}}\r\nok\r\n'
)


def test_get_lstat_parses_rx_event_counters():
    dev, ser = make_scripted({"lstat": LSTAT_REPLY})
    stat = dev.get_lstat()
    assert stat["SFDD"] == 12
    assert stat["CRCB"] == 7
    assert stat["PHE"] == 5
    assert stat["CRCG"] == 3


def test_get_lstat_returns_none_when_no_block():
    dev, ser = make_scripted({})  # board says nothing
    assert dev.get_lstat() is None


# --- active-TX CLI helpers (uwb-qorvo-1hu.1) ---------------------------------

UWBCFG_REPLY = (
    b'JS00BE{"UWB PARAM":{\r\n"CHAN":9,\r\n"PLEN":64,\r\n"PAC":8,\r\n'
    b'"TXCODE":9,\r\n"RXCODE":9,\r\n"SFDTYPE":3,\r\n"DATARATE":6810,\r\n'
    b'"PHRMODE":0,\r\n"PHRRATE":0,\r\n"SFDTO":65,\r\n"STSMODE":0,\r\n'
    b'"STSLEN":64,\r\n"PDOAMODE":1}}\r\nok\r\n'
)


def test_set_uwbcfg_overrides_given_params_preserving_rest():
    dev, ser = make_scripted({"uwbcfg": UWBCFG_REPLY})
    assert dev.set_uwbcfg(CHAN=5, TXCODE=10, RXCODE=10) is True
    # only CHAN, TXCODE, RXCODE change; the other 10 fields are preserved
    assert b"uwbcfg 5 64 8 10 10 3 6810 0 0 65 0 64 1\r\n" in bytes(ser.tx)


def test_set_uwbcfg_single_param_matches_set_channel_shape():
    dev, ser = make_scripted({"uwbcfg": UWBCFG_REPLY})
    assert dev.set_uwbcfg(CHAN=5) is True
    assert b"uwbcfg 5 64 8 9 9 3 6810 0 0 65 0 64 1\r\n" in bytes(ser.tx)


def test_set_uwbcfg_returns_false_when_no_cfg():
    dev, ser = make_scripted({})  # board never answers the uwbcfg query
    assert dev.set_uwbcfg(CHAN=5) is False
    # must not emit a set line if it couldn't read the current config
    assert b"uwbcfg 5 " not in bytes(ser.tx)


def test_start_initf_no_args_sends_bare_initf():
    dev, ser = make_device()
    dev.start_initf()
    assert b"initf\r\n" in bytes(ser.tx)
    assert dev.mode == "INITF"


def test_start_initf_appends_key_value_flags():
    dev, ser = make_device()
    dev.start_initf(CHAN=9, PCODE=10, ID=42)
    assert b"initf -CHAN=9 -PCODE=10 -ID=42\r\n" in bytes(ser.tx)
    assert dev.mode == "INITF"


def test_start_respf_no_args_and_flags():
    dev, ser = make_device()
    dev.start_respf()
    assert b"respf\r\n" in bytes(ser.tx)
    dev.start_respf(CHAN=9, PCODE=10, PADDR=1)
    assert b"respf -CHAN=9 -PCODE=10 -PADDR=1\r\n" in bytes(ser.tx)
    assert dev.mode == "RESPF"


def test_tcfm_bare_when_no_args():
    dev, ser = make_device()
    dev.tcfm()
    assert bytes(ser.tx) == b"tcfm\r\n"
    assert dev.mode == "TCFM"


def test_tcfm_count_and_interval_positional():
    dev, ser = make_device()
    dev.tcfm(count=100, interval=5)
    assert b"tcfm 100 5\r\n" in bytes(ser.tx)
    assert dev.mode == "TCFM"
