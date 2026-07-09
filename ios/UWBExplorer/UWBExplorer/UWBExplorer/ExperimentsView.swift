import SwiftUI

/// The `X<exp><action>` experiment-control opcodes the app writes to the
/// board's 6e5f control characteristic. These must stay byte-identical to the
/// shared grammar pinned in `uwb_explorer/experiments/control.py` (and
/// `docs/EXPERIMENTS.md`) — the Pi dispatcher parses exactly these strings.
///
/// Only the bare 3-char opcode ("X" + exp letter + action char) lives here.
/// Runtime args (" chan=9,pcode=10", " payload=deadbeef") are appended by the
/// caller and are out of scope for this hub.
enum ExpOpcode {
    // scanner (S)
    static let scannerStart  = "XS1"
    static let scannerStop   = "XS0"
    static let scannerStatus = "XS?"
    // transponder (T)
    static let transponderStart  = "XT1"
    static let transponderStop   = "XT0"
    static let transponderStatus = "XT?"
    // beacon (B)
    static let beaconStart  = "XB1"
    static let beaconStop   = "XB0"
    static let beaconStatus = "XB?"
    // fuzzer (Z)
    static let fuzzerStart  = "XZ1"
    static let fuzzerStop   = "XZ0"
    static let fuzzerStatus = "XZ?"
}

/// Hub listing the four on-board UWB experiments. Each row drills into a
/// placeholder control screen with Start / Stop / Status buttons that write
/// the bare opcode to the board — argument entry lands in a later bead.
struct ExperimentsView: View {
    @EnvironmentObject var ble: BLEManager

    var body: some View {
        NavigationStack {
            List {
                Section {
                    NavigationLink { ScannerExperimentView() } label: {
                        experimentRow("Scanner", "dot.radiowaves.left.and.right",
                                      "Sweep preamble codes hunting for UWB transmitters.")
                    }
                    NavigationLink { TransponderExperimentView() } label: {
                        experimentRow("Transponder", "arrow.left.arrow.right",
                                      "Reply to interrogations to probe two-way ranging.")
                    }
                    NavigationLink { BeaconExperimentView() } label: {
                        experimentRow("Beacon", "antenna.radiowaves.left.and.right",
                                      "Emit a periodic UWB frame with a chosen payload.")
                    }
                    NavigationLink { FuzzerExperimentView() } label: {
                        experimentRow("Fuzzer", "waveform.path.ecg",
                                      "Send malformed / varied frames to stress a receiver.")
                    }
                } footer: {
                    Text("Each experiment runs on the board and is driven over Bluetooth. Use only against equipment you own or are authorized to test.")
                }
            }
            .navigationTitle("Experiments")
        }
    }

    private func experimentRow(_ title: String, _ symbol: String, _ blurb: String) -> some View {
        HStack(spacing: 12) {
            Image(systemName: symbol)
                .font(.title3)
                .foregroundStyle(.tint)
                .frame(width: 28)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.headline)
                Text(blurb).font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 2)
    }
}

// MARK: - Per-experiment control screens
//
// Placeholders for this bead: a title, a one-line description, and the three
// verb buttons wired to the board. Argument entry (channels, payloads, fuzz
// parameters) is deliberately deferred.

/// Shared Start / Stop / Status control block. `start`/`stop`/`status` are the
/// bare opcodes for this experiment; each button writes one to the board.
private struct ExperimentControls: View {
    @EnvironmentObject var ble: BLEManager
    let start: String
    let stop: String
    let status: String

    var body: some View {
        HStack(spacing: 10) {
            Button {
                ble.sendExperiment(start)
            } label: {
                Label("Start", systemImage: "play.fill").frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)

            Button {
                ble.sendExperiment(stop)
            } label: {
                Label("Stop", systemImage: "stop.fill").frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .tint(.red)

            Button {
                ble.sendExperiment(status)
            } label: {
                Label("Status", systemImage: "info.circle").frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .tint(.secondary)
        }
        .disabled(!ble.isConnected)
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 14).fill(.ultraThinMaterial))
    }
}

/// Common scaffold: a description card, the control block, and an optional
/// extra caption (used by the fuzzer for its authorization notice).
private struct ExperimentDetail<Extra: View>: View {
    let title: String
    let blurb: String
    let start: String
    let stop: String
    let status: String
    @ViewBuilder var extra: () -> Extra

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text(blurb)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(14)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(RoundedRectangle(cornerRadius: 14).fill(.ultraThinMaterial))

                ExperimentControls(start: start, stop: stop, status: status)

                extra()
            }
            .padding()
        }
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
    }
}

extension ExperimentDetail where Extra == EmptyView {
    init(title: String, blurb: String, start: String, stop: String, status: String) {
        self.init(title: title, blurb: blurb, start: start, stop: stop,
                  status: status, extra: { EmptyView() })
    }
}

struct ScannerExperimentView: View {
    var body: some View {
        ExperimentDetail(
            title: "Scanner",
            blurb: "Sweeps preamble codes looking for any UWB transmitter within earshot, reporting what it hears. Good first pass to see what's on the air.",
            start: ExpOpcode.scannerStart,
            stop: ExpOpcode.scannerStop,
            status: ExpOpcode.scannerStatus)
    }
}

struct TransponderExperimentView: View {
    var body: some View {
        ExperimentDetail(
            title: "Transponder",
            blurb: "Replies to incoming interrogation frames so you can probe how an initiator behaves in a two-way ranging exchange.",
            start: ExpOpcode.transponderStart,
            stop: ExpOpcode.transponderStop,
            status: ExpOpcode.transponderStatus)
    }
}

struct BeaconExperimentView: View {
    var body: some View {
        ExperimentDetail(
            title: "Beacon",
            blurb: "Emits a periodic UWB frame carrying a chosen payload — a repeatable transmitter for testing receivers and range.",
            start: ExpOpcode.beaconStart,
            stop: ExpOpcode.beaconStop,
            status: ExpOpcode.beaconStatus)
    }
}

struct FuzzerExperimentView: View {
    var body: some View {
        ExperimentDetail(
            title: "Fuzzer",
            blurb: "Sends malformed and varied frames to stress-test how a UWB receiver handles unexpected input.",
            start: ExpOpcode.fuzzerStart,
            stop: ExpOpcode.fuzzerStop,
            status: ExpOpcode.fuzzerStatus) {
                Label("Authorized targets only — use this against equipment you own or have explicit permission to test.",
                      systemImage: "exclamationmark.shield")
                    .font(.caption)
                    .foregroundStyle(.orange)
                    .padding(12)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(RoundedRectangle(cornerRadius: 12).fill(Color.orange.opacity(0.12)))
            }
    }
}

#Preview {
    ExperimentsView().environmentObject(BLEManager())
}
