# UWB Explorer — iOS app

A SwiftUI app that connects to the Pi's UWB BLE peripheral over Bluetooth (no
WiFi) and shows the live "Geiger counter": activity level, frame-events/sec,
sparkline, channel/preamble, and decoded-frame count. Auto-reconnects.

It talks to the Pi service in `uwb_explorer/ble.py` — the UUIDs and the JSON
wire format (`uwb_explorer/blecodec.py`) are pinned on both sides.

## Files
- `UWBExplorerApp.swift` — app entry point
- `BLEManager.swift` — CoreBluetooth central: scan → connect → subscribe → decode
- `UWBState.swift` — Codable model of the BLE JSON payload + level→colour
- `ContentView.swift` — the gauge UI (mirrors the web dashboard)

## Build it (Xcode)
1. **File → New → Project → iOS → App.** Name it `UWBExplorer`, Interface
   **SwiftUI**, Language **Swift**.
2. Delete the generated `ContentView.swift` and the `…App.swift`, then drag the
   four `.swift` files from this folder into the project (check *Copy items if
   needed* and your app target).
3. **Add the Bluetooth permission** (required or iOS kills the app on launch):
   select the target → **Info** tab → add key
   **Privacy - Bluetooth Always Usage Description**
   (`NSBluetoothAlwaysUsageDescription`) with a value like
   `Reads live UWB activity from the Explorer board.`
4. Set your Team under **Signing & Capabilities** (a free personal team is fine
   for running on your own iPhone).
5. **Run on a real iPhone** — CoreBluetooth does not work in the Simulator.

## Use it
- Make sure the Pi service is running and advertising (`systemctl status
  uwb-dashboard` on the Pi shows `BLE peripheral 'UWB' advertising …`).
- Launch the app near the Pi; it scans for the service UUID, connects, and the
  meter goes live. Your phone keeps its normal internet the whole time.
- It reads `status: waiting` until the DWM board is on the Pi's J20 port, then
  flips to live — same behaviour as the web dashboard.

## Sanity-check the peripheral without the app
Install **nRF Connect** (free, App Store) → Scan → look for **UWB** →
Connect → the service `6E5F0001-…` has one characteristic; tap the notify
(down-arrow) icon and you'll see the JSON payload updating. If nRF Connect sees
it, the app will too.
