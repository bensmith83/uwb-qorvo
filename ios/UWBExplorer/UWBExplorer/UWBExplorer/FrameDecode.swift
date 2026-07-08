import Foundation

/// One decoded field of an 802.15.4z frame, with the byte range it covers
/// so a hex pane can highlight it. A direct port of tools/dissect.py — the
/// decode contract is pinned by tests/test_dissect.py against the real
/// 69-byte AirTag capture (FC 0x2B49, dst 0x0001, 63-byte secured body,
/// FCS 0x585F). Only genuinely readable parts are decoded; a
/// Security-Enabled frame's body is reported as opaque, which is the honest
/// result — the header and FCS travel in the clear, only the STS body is
/// sealed without the session key.
struct FrameField: Identifiable, Hashable {
    let id = UUID()
    var name: String
    var value: String
    var off: Int
    var length: Int
    var children: [FrameField] = []
    var note: String = ""
}

enum FrameDecode {
    private static let types = [0: "Beacon", 1: "Data", 2: "Ack",
                                3: "MAC Command", 5: "Multipurpose"]
    private static let addr = [0: "None", 1: "Reserved",
                               2: "Short/16-bit", 3: "Extended/64-bit"]
    private static let ver = [0: "2003", 1: "2006",
                              2: "IEEE 802.15.4-2015 (4z)", 3: "Reserved"]

    /// Parse a hex string (the frame characteristic's "b" field) into a
    /// field tree. `total` is the frame's true byte length (the "n" field):
    /// the BLE path truncates "b" to the first 16 bytes but reports the real
    /// length, so pass it to keep the body/FCS honest rather than fabricated
    /// from the prefix. Returns [] if there aren't even 2 bytes for an FC.
    static func dissect(hex: String, total: Int? = nil) -> [FrameField] {
        dissect(bytes: bytesFromHex(hex), total: total)
    }

    static func dissect(bytes frame: [UInt8], total: Int? = nil) -> [FrameField] {
        guard frame.count >= 2 else { return [] }
        var out: [FrameField] = []
        let avail = frame.count
        let n = max(total ?? avail, avail)

        let fc = Int(frame[0]) | (Int(frame[1]) << 8)   // little-endian
        let ftype = fc & 0x7
        let sec = (fc >> 3) & 1
        let seqSup = (fc >> 8) & 1
        let ie = (fc >> 9) & 1
        let dstMode = (fc >> 10) & 3
        let version = (fc >> 12) & 3
        let srcMode = (fc >> 14) & 3
        let panC = (fc >> 6) & 1

        func bits(_ desc: String, _ val: String) -> FrameField {
            FrameField(name: desc, value: val, off: 0, length: 2)
        }
        let b = { (v: Int) in String(v) }
        let fcf = FrameField(
            name: "Frame Control Field",
            value: String(format: "0x%04X", fc), off: 0, length: 2,
            children: [
                bits(".... .... .... .001 = Frame Type", "\(types[ftype] ?? "?") (\(ftype))"),
                bits(".... .... .... \(b(sec))... = Security Enabled", sec == 1 ? "True" : "False"),
                bits(".... .... ...\(b((fc>>4)&1)). .... = Frame Pending", (fc>>4)&1 == 1 ? "True" : "False"),
                bits(".... .... ..\(b((fc>>5)&1)).. .... = Ack Request", (fc>>5)&1 == 1 ? "True" : "False"),
                bits(".... .... .\(b(panC))... .... = PAN ID Compression", panC == 1 ? "True" : "False"),
                bits(".... ...\(b(seqSup)). .... .... = Seq Number Suppression", seqSup == 1 ? "True" : "False"),
                bits(".... ..\(b(ie)).. .... .... = IE Present", ie == 1 ? "True" : "False"),
                bits(".... \(bin2(dstMode)).. .... .... = Dest Addressing Mode", "\(addr[dstMode] ?? "?") (0x\(String(dstMode, radix: 16, uppercase: true)))"),
                bits("..\(bin2(version)) .... .... .... = Frame Version", ver[version] ?? "?"),
                bits("\(bin2(srcMode)).. .... .... .... = Source Addressing Mode", "\(addr[srcMode] ?? "?") (0x\(String(srcMode, radix: 16, uppercase: true)))"),
            ])
        out.append(fcf)

        var pos = 2
        if seqSup == 0, pos < frame.count {
            out.append(FrameField(name: "Sequence Number", value: String(frame[pos]), off: pos, length: 1))
            pos += 1
        }

        // 802.15.4-2015 PAN/address presence (single-address, compressed
        // case). Mirrors dissect.py: with a dst address and no src address,
        // PAN-ID-compression omits the PAN; otherwise a dst PAN precedes a
        // dst address. (The version-2 branch there resolves to the same.)
        let dstPan = (dstMode != 0 && srcMode == 0) ? (panC == 0) : (dstMode != 0)
        if dstPan && pos + 2 <= frame.count {
            out.append(FrameField(name: "Destination PAN ID",
                                  value: String(format: "0x%04X", le16(frame, pos)),
                                  off: pos, length: 2))
            pos += 2
        }
        if dstMode == 2 && pos + 2 <= frame.count {
            out.append(FrameField(name: "Destination Address",
                                  value: String(format: "0x%04X", le16(frame, pos)),
                                  off: pos, length: 2))
            pos += 2
        } else if dstMode == 3 && pos + 8 <= frame.count {
            out.append(FrameField(name: "Destination Address",
                                  value: hexColons(frame, pos, 8, reversed: true),
                                  off: pos, length: 8))
            pos += 8
        }

        let fcsLen = 2
        let bodyLen = n - pos - fcsLen
        if bodyLen > 0 {
            let captured = max(0, min(bodyLen, avail - pos))
            var note = sec == 1
                ? "Encrypted / authenticated (Security Enabled = True): auxiliary security header, STS-scrambled ranging data and payload. Opaque without the session key."
                : "Information elements and MAC payload."
            let value: String
            if captured < bodyLen {
                note += " Only the first \(avail) B of this \(n) B frame were carried over BLE — capture the full frame (Pi USB path) to see the whole body."
                value = "\(bodyLen) bytes (\(captured) captured)"
            } else {
                value = "\(bodyLen) bytes"
            }
            out.append(FrameField(name: sec == 1 ? "Secured Payload" : "MAC Payload",
                                  value: value, off: pos, length: bodyLen, note: note))
            pos += bodyLen
        }

        if pos + fcsLen <= n {
            if pos + fcsLen <= avail {
                out.append(FrameField(name: "FCS (CRC-16)",
                                      value: String(format: "0x%04X", le16(frame, pos)),
                                      off: pos, length: 2,
                                      note: "Frame check sequence as received. STS-secured frames fail this on a passive listener — the radio flags them CRC-bad — so treat it as the raw trailer, not a validated checksum."))
            } else {
                out.append(FrameField(name: "FCS (CRC-16)", value: "not captured",
                                      off: pos, length: 2,
                                      note: "At byte offset \(pos), beyond the first \(avail) B carried over BLE — capture the full frame (Pi USB path) to read it."))
            }
        }
        return out
    }

    // MARK: - helpers

    private static func bin2(_ v: Int) -> String {
        let s = String(v & 3, radix: 2)
        return String(repeating: "0", count: 2 - s.count) + s
    }

    private static func le16(_ f: [UInt8], _ i: Int) -> Int {
        Int(f[i]) | (Int(f[i + 1]) << 8)
    }

    private static func hexColons(_ f: [UInt8], _ i: Int, _ n: Int, reversed: Bool) -> String {
        var slice = Array(f[i..<i + n])
        if reversed { slice.reverse() }
        return slice.map { String(format: "%02x", $0) }.joined(separator: ":")
    }

    static func bytesFromHex(_ hex: String) -> [UInt8] {
        var out: [UInt8] = []
        var i = hex.startIndex
        while let j = hex.index(i, offsetBy: 2, limitedBy: hex.endIndex), j <= hex.endIndex {
            if let v = UInt8(hex[i..<j], radix: 16) { out.append(v) }
            i = j
            if i == hex.endIndex { break }
        }
        return out
    }
}
