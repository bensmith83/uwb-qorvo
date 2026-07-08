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
    /// Recent per-notification hit counts, for the sparkline.
    @Published var history: [Int] = []
    /// Latest received UWB frame (nil until the board hears one).
    @Published var lastFrame: UWBFrame?

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

    private func writeCtrl(_ cmd: String) {
        guard let p = peripheral, let ctrl = ctrlChar else { return }
        p.writeValue(Data(cmd.utf8), for: ctrl, type: .withResponse)
    }

    override init() {
        super.init()
        central = CBCentralManager(delegate: self, queue: nil)
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
        for ch in s.characteristics ?? [] {
            switch ch.uuid {
            case Self.charUUID, Self.frameCharUUID:
                p.setNotifyValue(true, for: ch)
                p.readValue(for: ch)
            case Self.ctrlCharUUID:
                ctrlChar = ch
            default:
                break
            }
        }
    }

    func peripheral(_ p: CBPeripheral, didUpdateValueFor ch: CBCharacteristic, error: Error?) {
        guard let data = ch.value else { return }
        if ch.uuid == Self.frameCharUUID {
            // Initial value is "{}" — all-nil decode means no frame yet.
            guard let frame = try? JSONDecoder().decode(UWBFrame.self, from: data),
                  frame.seq != nil else { return }
            DispatchQueue.main.async { self.lastFrame = frame }
            return
        }
        guard let decoded = try? JSONDecoder().decode(UWBState.self, from: data) else { return }
        DispatchQueue.main.async {
            self.state = decoded
            self.history.append(decoded.hits ?? 0)
            if self.history.count > 90 {
                self.history.removeFirst(self.history.count - 90)
            }
        }
    }
}
