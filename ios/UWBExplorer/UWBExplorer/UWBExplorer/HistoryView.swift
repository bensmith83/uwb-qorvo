import SwiftUI

/// A persisted log of every frame the board reported — the bytes and
/// signal snapshots from past interactions, kept across app restarts.
struct HistoryView: View {
    @EnvironmentObject var ble: BLEManager

    var body: some View {
        NavigationStack {
            Group {
                if ble.frameHistory.isEmpty {
                    ContentUnavailableView(
                        "No frames yet",
                        systemImage: "clock.arrow.circlepath",
                        description: Text("Point the board at UWB traffic (an AirTag precision-find). Captured frames show up here and are kept between sessions."))
                } else {
                    List {
                        ForEach(ble.frameHistory) { rec in
                            NavigationLink(value: rec) { rowLabel(rec) }
                        }
                    }
                    .navigationDestination(for: FrameRecord.self) { rec in
                        FrameDetailView(rec: rec)
                    }
                }
            }
            .navigationTitle("History")
            .toolbar {
                if !ble.frameHistory.isEmpty {
                    Button(role: .destructive) { ble.clearHistory() } label: {
                        Image(systemName: "trash")
                    }
                }
            }
        }
    }

    private func rowLabel(_ rec: FrameRecord) -> some View {
        let f = rec.frame
        return HStack(spacing: 12) {
            Image(systemName: f.isEncrypted ? "lock.fill" : "doc.text")
                .foregroundStyle(f.isEncrypted ? .orange : .green)
                .frame(width: 22)
            VStack(alignment: .leading, spacing: 2) {
                Text(f.isEncrypted ? "Encrypted UWB (STS)"
                                   : "Frame #\(f.seq ?? 0) · \(f.length ?? 0) B")
                    .font(.subheadline).bold()
                Text(rec.date.formatted(date: .abbreviated, time: .standard))
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            VStack(alignment: .trailing, spacing: 2) {
                if let c = rec.channel, let k = rec.code {
                    Text("ch \(c) · code \(k)").font(.caption2).foregroundStyle(.secondary)
                }
                if let rsl = f.rsl {
                    Text(String(format: "%.0f dBm", rsl)).font(.caption2).monospacedDigit()
                }
            }
        }
    }
}

/// Full byte-level detail for one captured frame — the "Wireshark view."
struct FrameDetailView: View {
    let rec: FrameRecord

    var body: some View {
        let f = rec.frame
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                header(f)
                if let hex = f.bytesHex, !hex.isEmpty {
                    section("PACKET BYTES") {
                        Text(hexDump(hex))
                            .font(.system(.footnote, design: .monospaced))
                            .textSelection(.enabled)
                    }
                }
                if f.isEncrypted {
                    section("WHY NO BYTES") {
                        Text("This was STS-encrypted UWB (AirTag / Nearby Interaction). The radio heard it but the frame failed its integrity check, so there are no readable payload bytes — only the failure signature below.")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                    section("SIGNATURE") {
                        grid([("Bad CRC", "\(f.crcb ?? 0)"), ("STS err", "\(f.stse ?? 0)"),
                              ("Hdr err", "\(f.phe ?? 0)"), ("Timeouts", "\(f.to ?? 0)")])
                    }
                } else {
                    section("SIGNAL") {
                        grid([("RSL", f.rsl.map { String(format: "%.1f dBm", $0) } ?? "–"),
                              ("First path", f.fsl.map { String(format: "%.1f dBm", $0) } ?? "–"),
                              ("CFO", f.cfoPPM.map { String(format: "%+.2f ppm", $0) } ?? "–"),
                              ("Length", f.length.map { "\($0) B" } ?? "–")])
                    }
                    if let ts = f.timestamp {
                        section("RX TIMESTAMP") {
                            Text(ts).font(.system(.footnote, design: .monospaced))
                        }
                    }
                }
            }
            .padding()
        }
        .navigationTitle("Frame")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func header(_ f: UWBFrame) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                Text(f.isEncrypted ? "Encrypted UWB" : "Frame #\(f.seq ?? 0)")
                    .font(.title2).bold()
                if f.crcFailed {
                    Text("CRC FAIL").font(.caption).bold()
                        .padding(.horizontal, 6).padding(.vertical, 2)
                        .background(Capsule().fill(Color.orange.opacity(0.25)))
                        .foregroundStyle(.orange)
                }
            }
            Text(rec.date.formatted(date: .long, time: .standard))
                .font(.caption).foregroundStyle(.secondary)
            if let c = rec.channel, let k = rec.code {
                Text("Channel \(c) · preamble code \(k)")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private func section(_ title: String, @ViewBuilder _ content: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.caption2).tracking(1).foregroundStyle(.secondary)
            content()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 12).fill(.ultraThinMaterial))
    }

    private func grid(_ items: [(String, String)]) -> some View {
        LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())],
                  alignment: .leading, spacing: 12) {
            ForEach(items, id: \.0) { item in
                VStack(alignment: .leading, spacing: 2) {
                    Text(item.0.uppercased()).font(.caption2).foregroundStyle(.secondary)
                    Text(item.1).font(.footnote).bold().monospacedDigit()
                }
            }
        }
    }

    /// "0000  41 88 0C AD DE …" offset-prefixed rows, 8 bytes each.
    private func hexDump(_ hex: String) -> String {
        var bytes: [String] = []
        var i = hex.startIndex
        while i < hex.endIndex {
            let j = hex.index(i, offsetBy: 2, limitedBy: hex.endIndex) ?? hex.endIndex
            bytes.append(String(hex[i..<j])); i = j
        }
        var out = ""
        for row in stride(from: 0, to: bytes.count, by: 8) {
            let slice = bytes[row..<min(row + 8, bytes.count)]
            out += String(format: "%04X  ", row) + slice.joined(separator: " ") + "\n"
        }
        return out.trimmingCharacters(in: .newlines)
    }
}
