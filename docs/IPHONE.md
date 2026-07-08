# iPhone ↔ DWM3001CDK walkthrough

The board's factory firmware is **Qorvo's Apple Nearby Interaction (QANI)
demo** — it already speaks the Apple NI accessory protocol. Verified: it
advertises over BLE as `DWM3001CDK (XXXXXXXX)` (suffix derived from the
chip's BLE MAC, unique per board).

## Requirements

- iPhone 11 or newer (U1/U2 chip; anything except SE models works)
- Board powered (either micro-USB port is fine for power alone)

## Steps

1. On the iPhone, install **"Qorvo Nearby Interaction"** from the App Store
   (publisher: Qorvo US, Inc. — free).
2. Open the app with Bluetooth on. It scans for accessories and should list
   **DWM3001CDK (XXXXXXXX)** (your board's own suffix).
3. Tap it to connect. The app negotiates a UWB session (BLE handshake →
   FiRa ranging) and shows **live distance** to the board, updating several
   times a second.
4. **Direction (azimuth/elevation arrow)** appears on iPhones that support
   camera-assisted direction finding (iPhone 14+ generally; needs
   `isCameraAssistanceEnabled`). Distance-only on older models.

## Play ideas

- Hot/cold hide-and-seek: hide the board, hunt it with the phone.
- Check precision: UWB TWR is typically ±10 cm — walk a tape measure.
- Multipath: watch readings in hallways vs open rooms, through walls,
  through your body (UWB at 6.5/8 GHz is attenuated by water/people).

## Troubleshooting

- Board not listed → is another host (the Pi) holding a BLE connection?
  `bluetoothctl disconnect <board MAC>` on the Pi, then rescan.
- After reflashing back to factory (`tools/flash.sh ni`), the name/ID stays
  the same (derived from chip MAC).
