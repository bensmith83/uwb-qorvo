"""Parser for the DWM3001CDK CLI firmware output.

Formats verified against DWM3001CDK_SDK_Developer_Guide_0.1.1.pdf (samples
quoted verbatim in docs/vendor/guide.txt):

- Ranging lines (INITF/RESPF):
  {"Block":0, "results":[{"Addr":"0x0001","Status":"Ok","D_cm":9,
   "LPDoA_deg":113.38,"LAoA_deg":46.35,"LFoM":0,"RAoA_deg":-59.33,
   "CFO_ppm":-0.58}]}
- Listener frames (LISTENER2), length-prefixed pseudo-JSON — the LSTN array
  holds UNQUOTED hex bytes, so json.loads() cannot handle it:
  JS00EF{"LSTN":[49,2B,01,...,58],"TS":"0xCE99FA8D","O":253}
- Status/info blocks: JS010F{"Info":{...}} (valid JSON after the JSxxxx
  prefix, mostly)
- Plain "ok" / "error" acks.
"""

from uwb_explorer.parser import parse_line, RangingResult, ListenerFrame, InfoBlock, Ack


def test_ranging_line_single_responder():
    line = ('{"Block":7, "results":[{"Addr":"0x0001","Status":"Ok","D_cm":9,'
            '"LPDoA_deg":113.38,"LAoA_deg":46.35,"LFoM":0,"RAoA_deg":-59.33,'
            '"CFO_ppm":-0.58}]}')
    ev = parse_line(line)
    assert isinstance(ev, RangingResult)
    assert ev.block == 7
    (r,) = ev.results
    assert r.addr == "0x0001"
    assert r.status == "Ok"
    assert r.distance_cm == 9
    assert r.aoa_azimuth_deg == 46.35
    assert r.cfo_ppm == -0.58


def test_ranging_line_error_status_has_no_distance():
    line = '{"Block":12, "results":[{"Addr":"0x0001","Status":"Err"}]}'
    ev = parse_line(line)
    (r,) = ev.results
    assert r.status == "Err"
    assert r.distance_cm is None


def test_listener_frame_pseudo_json_hex_array():
    line = ('JS00EF{"LSTN":[49,2B,01,00,26,13,00,FF,18,5A],'
            '"TS":"0xCE99FA8D","O":253}')
    ev = parse_line(line)
    assert isinstance(ev, ListenerFrame)
    assert ev.payload == bytes.fromhex("492b01002613 00ff185a".replace(" ", ""))
    assert ev.timestamp == 0xCE99FA8D
    assert ev.offset == 253


def test_info_block_js_prefixed_valid_json():
    line = ('JS010F{"Info":{"Device":"DWM3001CDK","Current App":"STOP",'
            '"Version":"0.1.1","Apps":["LISTENER2","INITF"]}}')
    ev = parse_line(line)
    assert isinstance(ev, InfoBlock)
    assert ev.data["Info"]["Current App"] == "STOP"


def test_uwb_param_block():
    line = 'JS00BE{"UWB PARAM":{"CHAN":9,"PLEN":64,"STSMODE":0,"PDOAMODE":1}}'
    ev = parse_line(line)
    assert isinstance(ev, InfoBlock)
    assert ev.data["UWB PARAM"]["CHAN"] == 9


def test_ok_ack():
    ev = parse_line("ok")
    assert isinstance(ev, Ack)
    assert ev.ok


def test_error_ack():
    ev = parse_line("error")
    assert isinstance(ev, Ack)
    assert not ev.ok


def test_unparseable_line_returns_none():
    assert parse_line("Listener Top Application: Started") is None
    assert parse_line("") is None


def test_listener_frame_decodes_802154_mac_when_possible():
    # 0x49,0x2B = frame control of the guide's sample SP0 frame
    line = 'JS00EF{"LSTN":[49,2B,01,00,26,13,00,FF],"TS":"0x1","O":1}'
    ev = parse_line(line)
    # not asserting full MAC decode here, just that raw payload survives
    assert ev.payload[0] == 0x49
    assert len(ev.payload) == 8


# --- Variants found by protocol research (docs/cli-protocol.md) ---


def test_listener_frame_new_variant_ts4ns_rsl_fsl():
    line = ('JS00D7{"LSTN":[49,2B,00,00,26,13,A7,27],'
            '"TS4ns":"0x47F2D4D8","O":1224,"rsl":-64.71,"fsl":-64.95}')
    ev = parse_line(line)
    assert isinstance(ev, ListenerFrame)
    assert ev.timestamp == 0x47F2D4D8
    assert ev.offset == 1224
    assert ev.rssi_dbm == -64.71
    assert ev.first_path_dbm == -64.95


def test_old_listener_variant_has_none_signal_levels():
    line = 'JS00EF{"LSTN":[49,2B],"TS":"0xCE99FA8D","O":253}'
    ev = parse_line(line)
    assert ev.rssi_dbm is None
    assert ev.first_path_dbm is None


def test_ranging_cfo_100ppm_variant_normalized():
    line = ('{"Block":3, "results":[{"Addr":"0x0000","Status":"Ok","D_cm":61,'
            '"LPDoA_deg":0.00,"LAoA_deg":0.00,"LFoM":0,"RAoA_deg":0.00,'
            '"CFO_100ppm":-639}]}')
    ev = parse_line(line)
    (r,) = ev.results
    assert r.distance_cm == 61
    assert r.cfo_ppm == -6.39  # CFO_100ppm is in units of 0.01 ppm


def test_bare_results_array_without_block_wrapper():
    # SDK 1.0.2 compact style: a bare JSON array of measurements
    line = ('[{"Addr":"0x0000","Status":"Ok","D_cm":61,"LPDoA_deg":0.00,'
            '"LAoA_deg":0.00,"LFoM":0,"RAoA_deg":0.00,"CFO_100ppm":-639}]')
    ev = parse_line(line)
    assert isinstance(ev, RangingResult)
    assert ev.block is None
    assert ev.results[0].distance_cm == 61


def test_error_ack_with_trailing_space():
    ev = parse_line("error ")
    assert isinstance(ev, Ack)
    assert not ev.ok
