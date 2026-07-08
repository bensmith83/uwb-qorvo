import SwiftUI

/// Plain-English explainer for the UWB concepts the app surfaces:
/// channels, preamble codes, and the anatomy of an 802.15.4z frame.
struct LearnView: View {
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    intro
                    frameAnatomy
                    ForEach(parts) { part in partCard(part) }
                    channelCard
                    preambleCard
                    footer
                }
                .padding()
            }
            .navigationTitle("How UWB works")
        }
    }

    private var intro: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Reading the radio")
                .font(.title2).bold()
            Text("Ultra-wideband sends very short pulses across a wide slice of spectrum. Every transmission is a **frame** built from the parts below. The board can see all of them arrive; only the encrypted core stays sealed.")
                .font(.subheadline).foregroundStyle(.secondary)
        }
    }

    // A left-to-right map of the frame, colour-coded by what's readable.
    private var frameAnatomy: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("ANATOMY OF A FRAME").font(.caption2).tracking(1).foregroundStyle(.secondary)
            HStack(spacing: 3) {
                seg("Preamble", 2.2, .green)
                seg("SFD", 1, .green)
                seg("PHR", 1.2, .green)
                seg("STS", 2.4, .orange)
                seg("Payload", 1.6, .orange)
                seg("CRC", 0.9, .green)
            }
            HStack(spacing: 16) {
                legend(.green, "in the clear")
                legend(.orange, "encrypted")
            }
            .font(.caption2).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 12).fill(.ultraThinMaterial))
    }

    private func seg(_ name: String, _ flex: Double, _ color: Color) -> some View {
        Text(name)
            .font(.system(size: 10, weight: .semibold))
            .lineLimit(1).minimumScaleFactor(0.6)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 10)
            .background(RoundedRectangle(cornerRadius: 5).fill(color.opacity(0.22)))
            .overlay(RoundedRectangle(cornerRadius: 5).stroke(color.opacity(0.5)))
            .layoutPriority(flex)
    }

    private func legend(_ c: Color, _ t: String) -> some View {
        HStack(spacing: 6) {
            RoundedRectangle(cornerRadius: 3).fill(c.opacity(0.5)).frame(width: 11, height: 11)
            Text(t)
        }
    }

    private func partCard(_ p: Part) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Circle().fill(p.encrypted ? Color.orange : Color.green).frame(width: 8, height: 8)
                Text(p.name).font(.headline)
                Text(p.tag).font(.caption2).foregroundStyle(.secondary)
            }
            Text(p.text).font(.subheadline).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 12).fill(.ultraThinMaterial))
    }

    private var channelCard: some View {
        conceptCard("Channel", "the frequency band") {
            Text("A UWB **channel** is which chunk of spectrum the radio uses. This board's chip supports exactly two: **channel 5** (~6.5 GHz) and **channel 9** (~8.0 GHz). Apple's AirTag and iPhone ranging live on **channel 9**. Other UWB gear (some industrial/RTLS tags) uses 5. The radio listens to one channel at a time.")
        }
    }

    private var preambleCard: some View {
        conceptCard("Preamble code", "the lock-and-key") {
            Text("The preamble isn't data — it's a **pulse pattern** the receiver correlates against to notice a frame is starting. The exact pattern is picked by a **preamble code**. The receiver only \"hears\" a transmitter using the **same** code; on the wrong code the frame looks like noise. On channel 9 the legal codes are 9–12, and Apple uses **10/11/12** — which is why the board defaults to code 10, and why finding a new transmitter means trying codes one at a time.")
        }
    }

    private func conceptCard(_ title: String, _ sub: String,
                             @ViewBuilder _ body: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(title).font(.headline)
                Text(sub).font(.caption).foregroundStyle(.secondary)
            }
            body().font(.subheadline).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 12).fill(Color.accentColor.opacity(0.08)))
    }

    private var footer: some View {
        Text("The board passively listens — no pairing, no transmitting. It proves a UWB conversation is happening and shows everything that travels in the clear.")
            .font(.footnote).foregroundStyle(.secondary)
            .padding(.top, 4)
    }

    struct Part: Identifiable {
        let id = UUID()
        let name, tag, text: String
        let encrypted: Bool
    }

    private let parts: [Part] = [
        .init(name: "Preamble", tag: "visible",
              text: "The opening burst of known pulses. Its job is sync: it lets the receiver detect a frame is arriving and lock onto its timing. Read as a code, not bytes.",
              encrypted: false),
        .init(name: "SFD", tag: "visible",
              text: "Start-of-Frame Delimiter — a special short marker right after the preamble that says \"the preamble is over, real data starts now.\" It's how the receiver finds the exact byte boundary to start decoding.",
              encrypted: false),
        .init(name: "PHR", tag: "visible",
              text: "PHY Header — a few bits describing the frame that follows: how long it is and what data rate it uses. Decoding this far means the receiver got a real, well-formed frame (not just noise).",
              encrypted: false),
        .init(name: "STS", tag: "sealed",
              text: "Scrambled Timestamp Sequence — the encrypted heart of secure ranging. It's a pseudo-random pulse sequence only the paired devices can generate, so nobody else can spoof or read the distance. Visible as energy, unreadable as data.",
              encrypted: true),
        .init(name: "Payload", tag: "sealed",
              text: "Any message bytes the frame carries, protected by the session key. For an AirTag this is opaque ciphertext.",
              encrypted: true),
        .init(name: "CRC / FCS", tag: "visible",
              text: "Frame Check Sequence — a 16-bit checksum the radio appends so the receiver can tell if the frame arrived intact. Encrypted frames often fail this check on a passive listener, which is why they register as energy but not readable bytes.",
              encrypted: false),
    ]
}
