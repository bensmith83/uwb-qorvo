import SwiftUI

struct ContentView: View {
    @EnvironmentObject var ble: BLEManager

    var body: some View {
        let s = ble.state
        VStack(spacing: 16) {
            header

            // Big meter card
            VStack(spacing: 4) {
                Text(s.levelWord)
                    .font(.caption).bold()
                    .tracking(3)
                    .foregroundStyle(.secondary)
                Text("\(s.hits ?? 0)")
                    .font(.system(size: 84, weight: .heavy, design: .rounded))
                    .monospacedDigit()
                    .foregroundStyle(s.levelColor)
                    .contentTransition(.numericText())
                Text("UWB frame-events / sec")
                    .font(.caption).foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 28)
            .background(RoundedRectangle(cornerRadius: 24).fill(.ultraThinMaterial))
            .overlay(RoundedRectangle(cornerRadius: 24)
                .stroke(s.levelColor.opacity(ble.isConnected ? 0.9 : 0.15), lineWidth: 2))
            .shadow(color: s.levelColor.opacity(s.level == "high" ? 0.5 : 0), radius: 24)
            .animation(.easeInOut(duration: 0.25), value: s.level)

            Sparkline(values: ble.history, color: s.levelColor)
                .frame(height: 70)
                .background(RoundedRectangle(cornerRadius: 14).fill(.ultraThinMaterial))

            HStack(spacing: 10) {
                stat("Total heard", "\(s.total ?? 0)")
                stat("Peak / poll", "\(s.peak ?? 0)")
            }
            HStack(spacing: 10) {
                stat("Channel", s.channelText)
                stat("Preamble", s.pcodeText)
            }

            if let f = ble.lastFrame {
                frameCard(f, tint: s.levelColor)
            }

            Text(note)
                .font(.footnote)
                .foregroundStyle((s.decoded ?? 0) > 0 ? s.levelColor : .secondary)
                .multilineTextAlignment(.center)
                .padding(.top, 4)

            Spacer()
        }
        .padding()
    }

    private var header: some View {
        HStack {
            Text("UWB Explorer").font(.title3).bold()
            Spacer()
            HStack(spacing: 6) {
                Circle()
                    .fill(ble.isConnected ? Color.green : Color.gray)
                    .frame(width: 8, height: 8)
                Text(statusText.uppercased())
                    .font(.caption2).tracking(1)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var statusText: String {
        if !ble.isConnected { return ble.connection }
        switch ble.state.status {
        case "live":  return "live"
        case "error": return "board error"
        default:      return "waiting for board"
        }
    }

    private var note: String {
        if !ble.isConnected { return "Bring the phone near the UWB unit." }
        switch ble.state.status {
        case "waiting": return "Plug the DWM3001CDK into the Pi (J20)…"
        default: break
        }
        if (ble.state.decoded ?? 0) > 0 { return "\(ble.state.decoded!) frame(s) fully decoded ✓" }
        if (ble.state.hits ?? 0) > 0 { return "UWB energy detected — frames hitting the antenna." }
        return "Point it at a car, a phone precision-finding, or an AirTag."
    }

    /// Details of the most recent UWB frame the board heard
    /// (frame characteristic 6e5f0003).
    private func frameCard(_ f: UWBFrame, tint: Color) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("LAST FRAME #\(f.seq ?? 0)")
                    .font(.caption2).tracking(1).foregroundStyle(.secondary)
                Spacer()
                Text(f.pathText)
                    .font(.caption2).bold().tracking(1)
                    .foregroundStyle(tint)
            }
            if !f.bytesSpaced.isEmpty {
                Text(f.bytesSpaced + ((f.length ?? 0) > 16 ? " …" : ""))
                    .font(.system(.footnote, design: .monospaced))
                    .lineLimit(2)
                    .foregroundStyle(.primary)
            }
            HStack(spacing: 12) {
                frameStat("RSL", f.rslText)
                frameStat("First path", f.fslText)
                frameStat("CFO", f.cfoText)
                frameStat("Len", f.length.map { "\($0) B" } ?? "–")
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 14).fill(.ultraThinMaterial))
        .animation(.easeInOut(duration: 0.2), value: f.seq)
    }

    private func frameStat(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title.uppercased()).font(.caption2).tracking(1).foregroundStyle(.secondary)
            Text(value).font(.footnote).bold().monospacedDigit()
        }
    }

    private func stat(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title.uppercased()).font(.caption2).tracking(1).foregroundStyle(.secondary)
            Text(value).font(.title2).bold().monospacedDigit()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 14).fill(.ultraThinMaterial))
    }
}

/// Simple bar sparkline of recent activity.
struct Sparkline: View {
    let values: [Int]
    let color: Color

    var body: some View {
        GeometryReader { geo in
            let maxV = max(1, values.max() ?? 1)
            let n = max(values.count, 1)
            let bw = geo.size.width / CGFloat(n)
            HStack(alignment: .bottom, spacing: 1) {
                ForEach(Array(values.enumerated()), id: \.offset) { _, v in
                    let h = max(2, CGFloat(v) / CGFloat(maxV) * (geo.size.height - 6))
                    RoundedRectangle(cornerRadius: 1)
                        .fill(v > 0 ? color : Color.gray.opacity(0.25))
                        .frame(width: max(1, bw - 1), height: h)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .bottomLeading)
        }
        .padding(6)
    }
}

#Preview {
    ContentView().environmentObject(BLEManager())
}
