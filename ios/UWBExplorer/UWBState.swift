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
}
