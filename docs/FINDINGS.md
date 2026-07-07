# Findings — DWM3001CDK UWB exploration (2026-07-07)

Consolidated technical results from building the UWB explorer and the
passive AirTag detection. Companion to `PLAN.md` (handoff/checklist),
`docs/cli-protocol.md` (protocol reference), `docs/EXPLORER.md` and
`docs/IPHONE.md` (usage).

## Hardware & toolchain

- **Board**: Qorvo DWM3001CDK = Nordic **nRF52833** (BLE MCU) + Qorvo
  **DW3110** UWB transceiver, onboard SEGGER J-Link OB. It is a self-
  contained module; no second board is required to program or run it.
- **Two micro-USB ports, different jobs:**
  - **J-Link port** (SEGGER, USB VID 0x1366): flashing + power. Under CLI
    firmware its serial line is **silent** — the console is NOT here.
  - **J20 native USB** (Nordic, USB VID 0x1915): the **CLI console**.
    Confirmed in firmware source (`board_interface_init()` -> `Usb.init()`).
  - For the explorer you want **both** cables plugged in at once.
- **Ports renumber on every replug** — J-Link and Nordic swap between
  `/dev/ttyACM0` and `ttyACM1`. Never hardcode; select by USB VID
  (`uwb_explorer/serialport.py` does this).
- **Flashing**: `pyocd` can't drive the J-Link OB (needs SEGGER's lib).
  **OpenOCD 0.12** (apt) works:
  `openocd -f interface/jlink.cfg -c "transport select swd" -f target/nordic/nrf52.cfg -c "init; ...; exit"`.
  Wrapped in `tools/flash.sh`.
- Native-USB CDC can re-enumerate under heavy load / rapid reopen (looks
  like a "wedge"); recover with a J-Link `reset run`. Firmware never bricked.
- nRF52833-CJAA A0, 512 KB flash / 128 KB RAM, no APPROTECT lock.

## Firmware personalities

- **Factory = QANI** (Qorvo Apple Nearby Interaction, FiRa stack, DW3XXX
  driver 06.00.14). Works out of the box with the iOS "Qorvo Nearby
  Interaction" app → live distance to the board. Advertises BLE as
  `DWM3001CDK (1613B863)`. Full backup taken first:
  `firmware/factory-backup.bin` (+ `factory-uicr.bin`); `flash.sh ni`
  restores it.
- **CLI** (`firmware/cli.hex`, DW3_QM33_SDK CLI-FreeRTOS 0.1.1): UART shell
  — INITF/RESPF (FiRa two-way ranging), **LISTENER2** (promiscuous sniffer),
  TCFM/TCWM (test TX), UWBCFG, LSTAT, DIAG, etc. Drives our dashboard.
- **UCI** (`fw-downloads/…UCI…hex`): host-driven binary protocol (Android /
  Qorvo Python path). Not used here.
- **This board reports "Found non-AOA DW3000"** at listener/ranging start:
  single-antenna variant → **distance yes, angle-of-arrival no** in CLI
  mode (LAoA/RAoA read ~0). The iPhone can still show direction on its side.

## CLI output formats (parser handles all)

- Ranging: `{"Block":N,"results":[{"Addr":"0x0001","Status":"Ok","D_cm":9,
  "LAoA_deg":..,"CFO_ppm":..}]}` — field names vary by SDK version
  (`CFO_ppm` vs `CFO_100ppm`; sometimes a bare array with no Block wrapper).
- Listener: `JS00EF{"LSTN":[49,2B,..hex..],"TS":"0x..","O":N[,"rsl":..,
  "fsl":..]}` — pseudo-JSON with **unquoted hex bytes**; `JSxxxx` prefix is
  the payload byte length.
- Info/config: `JS010F{"Info":{...}}`, `JS00BE{"UWB PARAM":{"CHAN":9,...}}`.
- LSTAT PHY counters: `JS0085{"RX Events":{"SFDD":..,"PHE":..,"CRCB":..,...}}`.

## ★ Passive AirTag detection (headline result)

Goal: sniff an iPhone-AirTag UWB ranging session with the board as a pure
eavesdropper (no pairing). **Achieved.**

- UWB is **not** always-on like BLE — a radio is silent unless a ranging
  session is active. Channels 5 & 9 read zero until real traffic appears.
- **Preamble code is the lock-and-key.** On channel 9 the board heard
  nothing on code 9 (even with the AirTag actively ranging), then lit up on
  codes 10-12:
  | code | preamble locks (SFDTO) | headers reached (PHE) | CRC |
  |------|------|------|-----|
  | 9  | 0 | 0 | 0 |
  | 10 | 52 | 555 | 1 |
  | 11 | 999 | 0 | 0 |
  | 12 | 252 | 209 | 0 |
  Code-selective response = proof of a real transmitter (Apple U1), not noise.
- **The encrypted core is un-capturable.** Apple ranging uses STS
  (Scrambled Timestamp Sequence, ~SP3). A 6-config byte hunt (codes 10/11 ×
  STS SP1/SP3 × two SFD types, full-dump) produced **zero** dumpable frames:
  the STS is consumed internally for timing and never exposed as payload;
  enabling STS matching without Apple's session key stops reception
  entirely. So: detection + RF fingerprint yes, plaintext/ciphertext bytes
  no. This is by design, not a rig limitation.
- Detection instrument: `tools/detect.py` (LSTAT counter deltas). Hunt:
  `tools/bytehunt.py`. Frame dissection: `tools/dissect.py`.
- Shareable report: `artifacts/airtag-capture.html` (+ rendered
  `airtag-uwb-capture.png`) — chart, preamble-code explainer, per-code
  breakdown, frame anatomy, Wireshark-style byte dissection, claims.

## Software (uwb_explorer/, TDD — 47 tests green)

- `transport.py` line framing over serial (+ `flush` to drop stale rx after
  streaming modes). `parser.py` typed events (both SDK formats). `mac.py`
  best-effort 802.15.4z header decode. `device.py` firmware detect + app
  control + `get_lstat()`/`set_channel()`/`start_listener(full=)`.
  `radar.py` rolling contact/stat model. `serialport.py` VID-based
  discovery. `tui.py` Textual dashboard. `console.py` raw REPL.
- Run: `./run.sh {dash|console|test|flash-cli|flash-ni}`.

## Gotchas cheat-sheet

- Board silent on ttyACM? You're on the J-Link port or wrong ttyACM number
  — use `find_cli_port()`. Or CDC re-enumerated → J-Link `reset run`.
- Reading config right after LISTENER returns junk/None → flush first
  (built into `Device` now).
- INITF perpetual TX_FAILED → CLI hex needs a full chip erase before flash
  (flash.sh does mass_erase).
