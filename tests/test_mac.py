"""802.15.4 MAC header decode for sniffed UWB frames (best-effort).

FiRa ranging frames are 802.15.4z data/multipurpose frames; SP3 frames have
no PHR payload visible, but SP0 frames carry a full MAC header. The guide's
sample listener frame starts 49 2B — frame control 0x2B49 (little-endian).
"""

from uwb_explorer.mac import decode_frame, FrameInfo


def test_guide_sample_frame_control():
    # 49 2B little-endian -> FC=0x2B49 = 0b0010101101001001:
    # type=1 (Data), security=1 (STS/MIC — FiRa frames are encrypted),
    # AR=0, PAN-ID-compress=1, seq-suppression=1, IE-present=1,
    # dst mode=2 (short), version=2 (802.15.4-2015), src mode=0
    frame = bytes.fromhex("492b01002613")
    info = decode_frame(frame)
    assert isinstance(info, FrameInfo)
    assert info.frame_type == "Data"
    assert info.version == 2
    assert info.ack_request is False
    assert info.security is True
    # v2 + compress + no src addr => PAN omitted; dst = 01 00 LE = 0x0001,
    # matching the guide's responder address
    assert info.dst == "0x0001"
    assert info.pan_id is None


def test_beacon_frame_type():
    # FC 0x8000 -> b0-2: type=0 beacon (LE bytes 00 80)
    info = decode_frame(bytes.fromhex("008000"))
    assert info.frame_type == "Beacon"


def test_blink_frame_type():
    # ISO/BPRF blink frames use the multipurpose frame type (0b101)
    info = decode_frame(bytes([0b101, 0x00, 0x01]))
    assert info.frame_type == "Multipurpose"


def test_short_addresses_extracted_when_present():
    # FC=0x8841: Data, PAN-ID compression, dst short, src short, ver=0
    # 41 88 seq(0x42) pan(0xDECA) dst(0x0001) src(0x0002)
    frame = bytes.fromhex("418842cade01000200")
    info = decode_frame(frame)
    assert info.seq == 0x42
    assert info.pan_id == 0xDECA
    assert info.dst == "0x0001"
    assert info.src == "0x0002"


def test_too_short_frame_returns_unknown():
    info = decode_frame(b"\x49")
    assert info.frame_type == "Unknown"
