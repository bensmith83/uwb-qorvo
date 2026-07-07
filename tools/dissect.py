#!/usr/bin/env python3
"""Dissect an IEEE 802.15.4z UWB frame into a field tree with byte offsets.

Produces the data a Wireshark-style view needs: each field's name, value,
and (byte_offset, byte_len) so a hex pane can highlight it. Only the parts
that are genuinely decodable are decoded; a Security-Enabled frame's body
is reported as opaque (that is the honest result, not a limitation to hide).
"""

from __future__ import annotations

from dataclasses import dataclass, field as dfield


@dataclass
class Field:
    name: str
    value: str
    off: int
    length: int
    children: list = dfield(default_factory=list)
    note: str = ""


_TYPES = {0: "Beacon", 1: "Data", 2: "Ack", 3: "MAC Command", 5: "Multipurpose"}
_ADDR = {0: "None", 1: "Reserved", 2: "Short/16-bit", 3: "Extended/64-bit"}
_VER = {0: "2003", 1: "2006", 2: "IEEE 802.15.4-2015 (4z)", 3: "Reserved"}


def dissect(frame: bytes) -> list[Field]:
    out: list[Field] = []
    fc = int.from_bytes(frame[:2], "little")
    ftype = fc & 0x7
    sec = (fc >> 3) & 1
    seq_sup = (fc >> 8) & 1
    ie = (fc >> 9) & 1
    dst_mode = (fc >> 10) & 3
    ver = (fc >> 12) & 3
    src_mode = (fc >> 14) & 3
    pan_c = (fc >> 6) & 1

    def bitrow(mask_desc, val):
        return Field(mask_desc, val, 0, 2)

    fcf = Field("Frame Control Field", f"0x{fc:04X}", 0, 2, children=[
        bitrow(".... .... .... .001 = Frame Type", f"{_TYPES.get(ftype,'?')} ({ftype})"),
        bitrow(f".... .... .... {sec}... = Security Enabled", "True" if sec else "False"),
        bitrow(f".... .... ...{(fc>>4)&1}. .... = Frame Pending", "True" if (fc>>4)&1 else "False"),
        bitrow(f".... .... ..{(fc>>5)&1}.. .... = Ack Request", "True" if (fc>>5)&1 else "False"),
        bitrow(f".... .... .{pan_c}... .... = PAN ID Compression", "True" if pan_c else "False"),
        bitrow(f".... ...{seq_sup}. .... .... = Seq Number Suppression", "True" if seq_sup else "False"),
        bitrow(f".... ..{ie}.. .... .... = IE Present", "True" if ie else "False"),
        bitrow(f".... {dst_mode:02b}.. .... .... = Dest Addressing Mode", f"{_ADDR[dst_mode]} (0x{dst_mode:X})"),
        bitrow(f"..{ver:02b} .... .... .... = Frame Version", f"{_VER[ver]}"),
        bitrow(f"{src_mode:02b}.. .... .... .... = Source Addressing Mode", f"{_ADDR[src_mode]} (0x{src_mode:X})"),
    ])
    out.append(fcf)

    pos = 2
    if not seq_sup:
        out.append(Field("Sequence Number", str(frame[pos]), pos, 1))
        pos += 1

    # 802.15.4-2015 PAN/address presence (single-address, compressed case)
    dst_pan = (not pan_c) if (dst_mode and not src_mode) else bool(dst_mode)
    if ver == 2 and dst_mode and not src_mode:
        dst_pan = not pan_c
    if dst_pan and pos + 2 <= len(frame):
        out.append(Field("Destination PAN ID",
                          f"0x{int.from_bytes(frame[pos:pos+2],'little'):04X}", pos, 2))
        pos += 2
    if dst_mode == 2 and pos + 2 <= len(frame):
        out.append(Field("Destination Address",
                          f"0x{int.from_bytes(frame[pos:pos+2],'little'):04X}", pos, 2))
        pos += 2
    elif dst_mode == 3 and pos + 8 <= len(frame):
        out.append(Field("Destination Address",
                          frame[pos:pos+8][::-1].hex(":"), pos, 8))
        pos += 8

    fcs_len = 2
    body_len = len(frame) - pos - fcs_len
    if body_len > 0:
        note = ("Encrypted / authenticated (Security Enabled = True): "
                "auxiliary security header, STS-scrambled ranging data and "
                "payload. Opaque without the session key."
                if sec else
                "Information elements and MAC payload.")
        out.append(Field("Secured Payload" if sec else "MAC Payload",
                         f"{body_len} bytes", pos, body_len, note=note))
        pos += body_len

    if pos + 2 <= len(frame):
        out.append(Field("FCS (CRC-16)",
                          f"0x{int.from_bytes(frame[pos:pos+2],'little'):04X}", pos, 2,
                          note="Frame check sequence, appended by the radio."))
    return out


REFERENCE_FRAME = bytes.fromhex(
    "492b01002613"
    "00ff185a08080808080808080808"
    "2a0000008c2d2f0d003f49111e685631"
    "46a9b106bfaf4b7cbe5565e8a9777a3f"
    "53cf256da448f78ba8560d76a0da275f58"
)


def _print(fields, depth=0):
    for f in fields:
        pad = "  " * depth
        print(f"{pad}{f.name}: {f.value}  [off {f.off}, {f.length}B]")
        _print(f.children, depth + 1)


if __name__ == "__main__":
    print(f"Frame: {len(REFERENCE_FRAME)} bytes  "
          f"TS=0xCE99FA8D  rsl=-64.7 dBm  (Qorvo DWM3001 LISTENER capture)\n")
    _print(dissect(REFERENCE_FRAME))
