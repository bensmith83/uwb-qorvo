"""Field-tree dissection of a captured 802.15.4z frame (tools/dissect.py).

Locks the contract the iOS Swift port (FrameDecode.swift) must reproduce:
the same field names, values, byte offsets and lengths the Pi capture
rendered into untracked-artifacts/airtag-capture.html. The reference frame
is the real 69-byte AirTag capture (FC 0x2B49, dst 0x0001, 63-byte secured
body, FCS 0x585F).
"""

from tools.dissect import dissect, REFERENCE_FRAME


def _by_name(fields, name):
    for f in fields:
        if f.name == name:
            return f
    raise AssertionError(f"field {name!r} not in {[x.name for x in fields]}")


def test_reference_frame_top_level_fields():
    fields = dissect(REFERENCE_FRAME)
    names = [f.name for f in fields]
    assert names == [
        "Frame Control Field",
        "Destination Address",
        "Secured Payload",
        "FCS (CRC-16)",
    ], names


def test_frame_control_field_decodes_airtag_header():
    fcf = _by_name(dissect(REFERENCE_FRAME), "Frame Control Field")
    assert fcf.value == "0x2B49"
    assert (fcf.off, fcf.length) == (0, 2)
    # the bit breakdown the Wireshark-style pane shows
    kids = {k.name.split("= ")[-1]: k.value for k in fcf.children}
    assert kids["Frame Type"] == "Data (1)"
    assert kids["Security Enabled"] == "True"
    assert kids["Frame Version"] == "IEEE 802.15.4-2015 (4z)"


def test_destination_address_offset_and_value():
    # seq-number suppression is set in this FC, so the dst address sits
    # immediately after the 2-byte FC (no seq byte, no dst PAN).
    dst = _by_name(dissect(REFERENCE_FRAME), "Destination Address")
    assert dst.value == "0x0001"
    assert (dst.off, dst.length) == (2, 2)


def test_secured_payload_is_opaque_body():
    body = _by_name(dissect(REFERENCE_FRAME), "Secured Payload")
    assert body.value == "63 bytes"
    assert body.off == 4
    assert body.length == 63
    assert "session key" in body.note


def test_fcs_is_last_two_bytes_as_received():
    fcs = _by_name(dissect(REFERENCE_FRAME), "FCS (CRC-16)")
    assert fcs.value == "0x585F"
    assert (fcs.off, fcs.length) == (67, 2)


def test_truncated_ble_capture_is_honest_not_fabricated():
    # The BLE path sends only the first 16 bytes but reports the true length
    # (69) separately. The header still decodes; the body and FCS must be
    # reported at their TRUE position/size and flagged as not carried, not
    # invented from the 16-byte prefix.
    prefix = REFERENCE_FRAME[:16]
    fields = dissect(prefix, total=len(REFERENCE_FRAME))

    fcf = _by_name(fields, "Frame Control Field")
    assert fcf.value == "0x2B49"                      # header intact
    dst = _by_name(fields, "Destination Address")
    assert dst.value == "0x0001"

    body = _by_name(fields, "Secured Payload")
    assert body.off == 4 and body.length == 63        # TRUE size, not 10
    assert "captured" in body.value                   # flagged as partial
    assert "over BLE" in body.note

    fcs = _by_name(fields, "FCS (CRC-16)")
    assert fcs.off == 67                              # TRUE offset, not 14
    assert fcs.value == "not captured"


def test_full_frame_still_decodes_fcs_when_total_matches():
    # total == len(frame): no truncation, identical to the default path.
    a = dissect(REFERENCE_FRAME)
    b = dissect(REFERENCE_FRAME, total=len(REFERENCE_FRAME))
    assert [(f.name, f.value, f.off, f.length) for f in a] == \
           [(f.name, f.value, f.off, f.length) for f in b]
