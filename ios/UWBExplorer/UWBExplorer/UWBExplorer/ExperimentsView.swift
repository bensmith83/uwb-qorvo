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

/// Real control UI for the active scanner. Unlike the other experiments it lets
/// the user pick which PHY combos to sweep — channels {5, 9} and preamble codes
/// {9, 10, 11, 12} — and composes a START opcode carrying those selections as
/// `channels=`/`pcodes=` args.
///
/// Wire format (see `docs/EXPERIMENTS.md` and `uwb_explorer/experiments`):
/// list values join with `;` because the shared grammar reserves `,` for the
/// `key=value` pair separator, so the default command is
/// `XS1 channels=5;9,pcodes=9;10;11;12`.
struct ScannerExperimentView: View {
    @EnvironmentObject var ble: BLEManager

    // Selectable PHY space, matching scanner.DEFAULT_CHANNELS / DEFAULT_PCODES.
    private static let allChannels = [5, 9]
    private static let allPcodes = [9, 10, 11, 12]

    // Default selection is everything on (mirrors the Pi-side defaults).
    @State private var selectedChannels: Set<Int> = [5, 9]
    @State private var selectedPcodes: Set<Int> = [9, 10, 11, 12]

    /// The composed START command, e.g. `XS1 channels=5;9,pcodes=9;10;11;12`.
    /// List values are joined with `;` (the `,` is the pair separator).
    private var startCommand: String {
        let chans = Self.allChannels
            .filter { selectedChannels.contains($0) }
            .map(String.init)
            .joined(separator: ";")
        let pcodes = Self.allPcodes
            .filter { selectedPcodes.contains($0) }
            .map(String.init)
            .joined(separator: ";")
        return ExpOpcode.scannerStart + " channels=" + chans + ",pcodes=" + pcodes
    }

    private var canStart: Bool {
        ble.isConnected && !selectedChannels.isEmpty && !selectedPcodes.isEmpty
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Sweeps the selected channels and preamble codes, actively polling each combo and reporting any UWB device that replies. Good first pass to see what's on the air.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(14)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(RoundedRectangle(cornerRadius: 14).fill(.ultraThinMaterial))

                selectionCard(title: "Channels",
                              values: Self.allChannels,
                              selection: $selectedChannels)
                selectionCard(title: "Preamble codes",
                              values: Self.allPcodes,
                              selection: $selectedPcodes)

                // Controls: Start composes the arg'd opcode; Stop/Status are bare.
                HStack(spacing: 10) {
                    Button {
                        ble.sendExperiment(startCommand)
                    } label: {
                        Label("Start", systemImage: "play.fill").frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(!canStart)

                    Button {
                        ble.sendExperiment(ExpOpcode.scannerStop)
                    } label: {
                        Label("Stop", systemImage: "stop.fill").frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                    .tint(.red)

                    Button {
                        ble.sendExperiment(ExpOpcode.scannerStatus)
                    } label: {
                        Label("Status", systemImage: "info.circle").frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                    .tint(.secondary)
                }
                .disabled(!ble.isConnected)
                .padding(14)
                .background(RoundedRectangle(cornerRadius: 14).fill(.ultraThinMaterial))

                // Preview of the exact wire string, so what gets sent is visible.
                Text(startCommand)
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)

                resultsCard
            }
            .padding()
        }
        .navigationTitle("Scanner")
        .navigationBarTitleDisplayMode(.inline)
    }

    /// A titled card of multi-select toggle chips over `values`.
    private func selectionCard(title: String,
                               values: [Int],
                               selection: Binding<Set<Int>>) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(title).font(.headline)
            HStack(spacing: 10) {
                ForEach(values, id: \.self) { value in
                    let isOn = selection.wrappedValue.contains(value)
                    Button {
                        if isOn {
                            selection.wrappedValue.remove(value)
                        } else {
                            selection.wrappedValue.insert(value)
                        }
                    } label: {
                        Text(String(value))
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 8)
                    }
                    .buttonStyle(.bordered)
                    .tint(isOn ? .accentColor : .secondary)
                }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 14).fill(.ultraThinMaterial))
    }

    /// Discovered-devices area. A board -> BLE results uplink does not exist
    /// yet (a later bead), so for now this is an honest placeholder rather than
    /// faked scan output.
    private var resultsCard: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Discovered devices").font(.headline)
            Text("Discovered devices will appear here once the board reports them over Bluetooth.")
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 14).fill(.ultraThinMaterial))
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
