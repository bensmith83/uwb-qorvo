"""Best-effort IEEE 802.15.4(z) MAC header decode for sniffed frames."""

from __future__ import annotations

from dataclasses import dataclass

_FRAME_TYPES = {
    0: "Beacon",
    1: "Data",
    2: "Ack",
    3: "MAC Command",
    5: "Multipurpose",
    6: "Fragment",
    7: "Extended",
}


@dataclass
class FrameInfo:
    frame_type: str
    version: int | None = None
    security: bool = False
    ack_request: bool = False
    seq: int | None = None
    pan_id: int | None = None
    dst: str | None = None
    src: str | None = None


def decode_frame(frame: bytes) -> FrameInfo:
    if len(frame) < 2:
        return FrameInfo(frame_type="Unknown")

    fc = int.from_bytes(frame[:2], "little")
    ftype = fc & 0x7
    info = FrameInfo(
        frame_type=_FRAME_TYPES.get(ftype, "Reserved"),
        security=bool(fc & 0x0008),
        ack_request=bool(fc & 0x0020),
        version=(fc >> 12) & 0x3,
    )

    if ftype == 5:  # multipurpose frames have a different FC layout
        return info

    pos = 2
    seq_suppressed = bool(fc & 0x0100)
    if not seq_suppressed and pos < len(frame):
        info.seq = frame[pos]
        pos += 1

    dst_mode = (fc >> 10) & 0x3
    src_mode = (fc >> 14) & 0x3
    pan_compress = bool(fc & 0x0040)

    # PAN ID presence: 802.15.4-2015 (version 2) redefines the compression
    # bit — with only one address present, compress=1 means the PAN ID is
    # omitted entirely. Pre-2015: dst PAN always precedes a dst address.
    if info.version == 2:
        if dst_mode and src_mode:
            dst_pan = True  # compress=1 omits only the src PAN
        elif dst_mode or src_mode:
            dst_pan = not pan_compress
        else:
            dst_pan = pan_compress
        src_pan = bool(dst_mode and src_mode and not pan_compress)
    else:
        dst_pan = bool(dst_mode)
        src_pan = bool(src_mode and not pan_compress)

    if dst_pan and pos + 2 <= len(frame):
        info.pan_id = int.from_bytes(frame[pos:pos + 2], "little")
        pos += 2
    if dst_mode:
        info.dst, pos = _short_or_ext(dst_mode, frame, pos)
    if src_pan and pos + 2 <= len(frame):
        info.pan_id = int.from_bytes(frame[pos:pos + 2], "little")
        pos += 2
    if src_mode:
        info.src, pos = _short_or_ext(src_mode, frame, pos)

    return info


def _short_or_ext(mode: int, data: bytes, pos: int) -> tuple[str | None, int]:
    if mode == 2 and pos + 2 <= len(data):
        return f"0x{int.from_bytes(data[pos:pos+2], 'little'):04x}", pos + 2
    if mode == 3 and pos + 8 <= len(data):
        return data[pos:pos + 8][::-1].hex(":"), pos + 8
    return None, pos
