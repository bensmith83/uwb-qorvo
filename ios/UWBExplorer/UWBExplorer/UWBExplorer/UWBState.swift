import SwiftUI

/// Mirrors the compact JSON pushed by the Pi's BLE characteristic
/// (uwb_explorer/blecodec.py). Short keys keep it inside one BLE MTU.
struct UWBState: Decodable {
    var status: String
    var level: String
    var hits: Int?
    var total: Int?
    var peak: Int?
    var decoded: Int?
    var channel: Int?
    var pcode: Int?

    enum CodingKeys: String, CodingKey {
        case status = "s", level = "l", hits = "h", total = "t"
        case peak = "p", decoded = "d", channel = "c", pcode = "k"
    }
}

extension UWBState {
    static let idle = UWBState(status: "waiting", level: "idle",
                               hits: 0, total: 0, peak: 0, decoded: 0,
                               channel: nil, pcode: nil)

    /// Colour for the current activity level, matching the web dashboard.
    var levelColor: Color {
        switch level {
        case "high":   return Color(red: 0.88, green: 0.32, blue: 0.24)  // red
        case "medium": return Color(red: 0.79, green: 0.60, blue: 0.18)  // amber
        case "low":    return Color(red: 0.18, green: 0.49, blue: 0.36)  // green
        default:       return Color.gray
        }
    }

    /// Human word for the level (matches the web UI's wording).
    var levelWord: String {
        switch level {
        case "high":   return "STRONG"
        case "medium": return "ACTIVE"
        case "low":    return "FAINT"
        default:       return "IDLE"
        }
    }

    var channelText: String { channel.map(String.init) ?? "–" }
    var pcodeText: String { pcode.map(String.init) ?? "–" }

    /// True while the board is auto-sweeping preamble codes hunting a
    /// transmitter (firmware status "scan").
    var isScanning: Bool { status == "scan" }
}

/// One received UWB frame, from the firmware's frame characteristic
/// (6e5f0003, firmware/ble/framefmt.c). The characteristic's initial
/// value is "{}", so every field is optional; `seq` nil means
/// "no frame heard yet".
struct UWBFrame: Decodable {
    var seq: Int?
    var length: Int?
    var bytesHex: String?
    var rsl: Double?     // received signal level, dBm
    var fsl: Double?     // first-path signal level, dBm
    var cfoPPM: Double?  // carrier frequency offset, ppm
    var timestamp: String?
    // encrypted / undecodable-energy marker (STS traffic, e.g. AirTag)
    var enc: Int?
    var phe: Int?        // PHY header errors
    var crcb: Int?       // bad-CRC frames
    var stse: Int?       // STS errors (the encryption tell)
    var to: Int?         // SFD/preamble/RX timeouts

    enum CodingKeys: String, CodingKey {
        case seq = "i", length = "n", bytesHex = "b"
        case rsl, fsl, cfoPPM = "o", timestamp = "ts"
        case enc, phe, crcb, stse, to
    }

    var isEncrypted: Bool { enc == 1 }
}

extension UWBFrame {
    /// First-path vs total power gap — the classic DW3xxx line-of-sight
    /// heuristic (APS006): small gap = direct path dominates.
    var pathText: String {
        guard let r = rsl, let f = fsl else { return "–" }
        let gap = r - f
        if gap < 6 { return "LIKELY LOS" }
        if gap > 10 { return "LIKELY NLOS" }
        return "MIXED PATH"
    }

    var rslText: String { rsl.map { String(format: "%.1f dBm", $0) } ?? "–" }
    var fslText: String { fsl.map { String(format: "%.1f dBm", $0) } ?? "–" }
    var cfoText: String { cfoPPM.map { String(format: "%+.2f ppm", $0) } ?? "–" }

    /// "41 88 0C AD DE …" — spaced hex for display.
    var bytesSpaced: String {
        guard let b = bytesHex, !b.isEmpty else { return "" }
        var out = ""
        var i = b.startIndex
        while i < b.endIndex {
            let j = b.index(i, offsetBy: 2, limitedBy: b.endIndex) ?? b.endIndex
            if !out.isEmpty { out += " " }
            out += b[i..<j]
            i = j
        }
        return out
    }
}
