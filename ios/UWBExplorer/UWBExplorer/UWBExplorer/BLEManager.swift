import CoreBluetooth
import Combine

/// Connects to the Pi's UWB BLE peripheral, subscribes to state notifications,
/// and republishes them for SwiftUI. Auto-reconnects on drop.
final class BLEManager: NSObject, ObservableObject {
    // Must match uwb_explorer/ble.py + firmware/ble/ble_app.c
    static let serviceUUID   = CBUUID(string: "6e5f0001-b5a3-f393-e0a9-e50e24dcca9e")
    static let charUUID      = CBUUID(string: "6e5f0002-b5a3-f393-e0a9-e50e24dcca9e")
    static let frameCharUUID = CBUUID(string: "6e5f0003-b5a3-f393-e0a9-e50e24dcca9e")
    static let ctrlCharUUID  = CBUUID(string: "6e5f0004-b5a3-f393-e0a9-e50e24dcca9e")

    @Published var state = UWBState.idle
    @Published var connection = "Starting…"
    @Published var isConnected = false
    /// How many of our 3 characteristics the current connection found. If
    /// it's < 3 after connecting, iOS handed us a stale cached service list
    /// (the classic "added characteristics later" trap) and the fix is to
    /// restart the phone.
    @Published var foundChars = 0
    /// True once a state notification has actually been received on this
    /// connection — distinguishes "connected & working" from "connected but
    /// the Bluetooth data pipe is stale."
    @Published var gotData = false
    /// Recent per-notification hit counts, for the sparkline.
    @Published var history: [Int] = []
    /// Latest received UWB frame (nil until the board hears one).
    @Published var lastFrame: UWBFrame?
    /// Persisted log of every distinct frame the board reported, newest
    /// first — survives app restarts so you can revisit past interactions.
    @Published var frameHistory: [FrameRecord] = []

    private let historyKey = "uwb.frameHistory.v1"
    private let historyCap = 300

    private var central: CBCentralManager!
    private var peripheral: CBPeripheral?
    private var ctrlChar: CBCharacteristic?

    /// Retune the board's UWB listener channel (5 or 9). The state JSON's
    /// "c" field confirms the switch a tick later.
    func setChannel(_ ch: Int) { writeCtrl("C\(ch)") }

    /// Lock the listener to one preamble code (9–12); stops auto-sweep.
    func setPreamble(_ code: Int) { writeCtrl("P\(code)") }

    /// Auto-sweep preamble codes and lock onto whichever hears traffic
    /// (true), or hold the current code (false).
    func setAutoScan(_ on: Bool) { writeCtrl(on ? "A" : "M") }

    /// Capture CRC-failed frames (the encrypted STS frames from an AirTag).
    /// Lets the byte card show the real frame bytes even though they fail
    /// their integrity check. Board default is off; re-sent on reconnect.
    @Published var captureFailed = false
    func setCaptureFailed(_ on: Bool) {
        captureFailed = on
        writeCtrl(on ? "F1" : "F0")
    }

    /// Ask the board to rotate its BLE address. The link drops and the app
    /// re-discovers the (now cache-free) device automatically. Handy if a
    /// client's GATT cache ever goes stale.
    func rotateAddress() { writeCtrl("N") }

    private func writeCtrl(_ cmd: String) {
        guard let p = peripheral, let ctrl = ctrlChar else { return }
        p.writeValue(Data(cmd.utf8), for: ctrl, type: .withResponse)
    }

    override init() {
        super.init()
        loadHistory()
        central = CBCentralManager(delegate: self, queue: nil)
    }

    func clearHistory() {
        frameHistory = []
        UserDefaults.standard.removeObject(forKey: historyKey)
    }

    private func loadHistory() {
        guard let data = UserDefaults.standard.data(forKey: historyKey),
              let recs = try? JSONDecoder().decode([FrameRecord].self, from: data)
        else { return }
        frameHistory = recs
    }

    private func saveHistory() {
        if let data = try? JSONEncoder().encode(frameHistory) {
            UserDefaults.standard.set(data, forKey: historyKey)
        }
    }

    /// Record a frame in history. A real decoded frame is always kept as
    /// its own entry (those are rare and precious); a run of
    /// encrypted-energy snapshots within 5 s collapses into one updating
    /// row so a single find doesn't flood the log.
    private func record(_ frame: UWBFrame) {
        let now = Date()
        if frame.isEncrypted, let first = frameHistory.first,
           first.frame.isEncrypted, now.timeIntervalSince(first.date) < 5 {
            frameHistory[0] = FrameRecord(id: first.id, date: now,
                                          channel: state.channel,
                                          code: state.pcode, frame: frame)
        } else {
            frameHistory.insert(FrameRecord(date: now, channel: state.channel,
                                            code: state.pcode, frame: frame), at: 0)
            if frameHistory.count > historyCap {
                frameHistory.removeLast(frameHistory.count - historyCap)
            }
        }
        saveHistory()
    }

    private func startScan() {
        guard central.state == .poweredOn else { return }
        isConnected = false
        connection = "Scanning for UWB…"
        central.scanForPeripherals(withServices: [Self.serviceUUID], options: nil)
    }
}

extension BLEManager: CBCentralManagerDelegate, CBPeripheralDelegate {
    func centralManagerDidUpdateState(_ c: CBCentralManager) {
        switch c.state {
        case .poweredOn:   startScan()
        case .poweredOff:  connection = "Bluetooth is off"
        case .unauthorized: connection = "Bluetooth not permitted"
        default:           connection = "Bluetooth unavailable"
        }
    }

    func centralManager(_ c: CBCentralManager, didDiscover p: CBPeripheral,
                        advertisementData: [String: Any], rssi RSSI: NSNumber) {
        peripheral = p
        p.delegate = self
        c.stopScan()
        connection = "Connecting…"
        c.connect(p, options: nil)
    }

    func centralManager(_ c: CBCentralManager, didConnect p: CBPeripheral) {
        connection = "Connected"
        isConnected = true
        foundChars = 0
        gotData = false
        p.discoverServices([Self.serviceUUID])
    }

    func centralManager(_ c: CBCentralManager, didFailToConnect p: CBPeripheral, error: Error?) {
        startScan()
    }

    func centralManager(_ c: CBCentralManager, didDisconnectPeripheral p: CBPeripheral, error: Error?) {
        isConnected = false
        connection = "Reconnecting…"
        startScan()
    }

    func peripheral(_ p: CBPeripheral, didDiscoverServices error: Error?) {
        for s in p.services ?? [] where s.uuid == Self.serviceUUID {
            p.discoverCharacteristics(
                [Self.charUUID, Self.frameCharUUID, Self.ctrlCharUUID], for: s)
        }
    }

    func peripheral(_ p: CBPeripheral, didDiscoverCharacteristicsFor s: CBService, error: Error?) {
        var n = 0
        for ch in s.characteristics ?? [] {
            switch ch.uuid {
            case Self.charUUID, Self.frameCharUUID:
                n += 1
                p.setNotifyValue(true, for: ch)
                p.readValue(for: ch)
            case Self.ctrlCharUUID:
                n += 1
                ctrlChar = ch
                // the board resets capture to off on each connect — restore
                if captureFailed { p.writeValue(Data("F1".utf8), for: ch, type: .withResponse) }
            default:
                break
            }
        }
        DispatchQueue.main.async { self.foundChars = n }
    }

    func peripheral(_ p: CBPeripheral, didUpdateValueFor ch: CBCharacteristic, error: Error?) {
        guard let data = ch.value else { return }
        if ch.uuid == Self.frameCharUUID {
            // Initial value is "{}" — all-nil decode means no frame yet.
            guard let frame = try? JSONDecoder().decode(UWBFrame.self, from: data),
                  frame.seq != nil else { return }
            DispatchQueue.main.async {
                self.lastFrame = frame
                self.record(frame)
            }
            return
        }
        guard let decoded = try? JSONDecoder().decode(UWBState.self, from: data) else { return }
        DispatchQueue.main.async {
            self.gotData = true
            self.state = decoded
            self.history.append(decoded.hits ?? 0)
            if self.history.count > 90 {
                self.history.removeFirst(self.history.count - 90)
            }
        }
    }
}
