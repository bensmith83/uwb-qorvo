# UWB Explorer — Qorvo DWM3001CDK

A UWB exploration rig built around the Qorvo DWM3001CDK (Nordic nRF52833 +
Qorvo DW3110 UWB transceiver), driven from a Raspberry Pi 4 over USB.

**Headline result:** it passively detects an Apple AirTag's ultra-wideband
ranging session in the air — no pairing — and there's a shareable report of
it in `artifacts/`. See `docs/FINDINGS.md`.

## Quick start

```bash
source venv/bin/activate            # or use ./venv/bin/python directly
./run.sh test                       # 68 tests
./run.sh flash-cli                  # put the CLI (explorer) firmware on the board
./run.sh dash                       # live terminal dashboard (needs J20 cable)
./run.sh web                        # phone web dashboard — the portable/con build
./run.sh flash-ni                   # restore factory iPhone (Nearby Interaction) firmware
```

## Three ways to see UWB on your phone

1. **Web dashboard** (working) — the Pi serves a phone-optimized live "Geiger
   counter" over WiFi (`uwb_explorer/web.py`). See `docs/CON-KIT.md`.
2. **BLE + native iOS app** — the Pi advertises the UWB state over Bluetooth so
   an iPhone app receives it with no WiFi. Pi peripheral: `uwb_explorer/ble.py`
   (+ unified `serve.py`); the SwiftUI app: `ios/`. *(BLE advertising on the Pi
   is still being sorted; the wire format and app are done.)*
3. **Board-only firmware** (in progress) — custom nRF52833 firmware so the
   DWM3001CDK runs the UWB listener **and** a BLE service itself: board +
   battery + phone, no Pi. This is the endgame con device.

**Take it to a con:** a battery-powered handheld that shows a live UWB "Geiger
counter" on your phone — see `docs/CON-KIT.md` (build a Pi Zero W + board +
battery unit; `sudo ./tools/con-setup.sh` makes it auto-start and broadcast its
own WiFi).

Passive AirTag / UWB hunt (needs the CLI firmware + J20 cable):
```bash
./venv/bin/python tools/detect.py --sweep --codes 10,11,12 --seconds 55
```

## Hardware

- **DWM3001CDK** — one module: nRF52833 MCU + DW3110 UWB radio + onboard
  SEGGER J-Link. Two micro-USB ports, **plug both in** for the explorer:
  - **J-Link port** (VID 0x1366) — flashing + power. Console is *not* here.
  - **J20 native USB** (Nordic VID 0x1915) — the **CLI console**.
  - Ports renumber on replug; tools find the board by USB vendor ID.
- Raspberry Pi 4 (this host) — flashing (OpenOCD), console, dashboard.
- iPhone — free "Qorvo Nearby Interaction" app for phone↔board ranging.

## Two firmware personalities (swap in seconds via `tools/flash.sh`)

1. **Nearby Interaction (QANI)** — the factory image; ranges against an
   iPhone's U1/U2. Live distance in Qorvo's iOS app. `flash.sh ni`.
2. **CLI** — UART shell: FiRa ranging (INITF/RESPF), **LISTENER2** sniffer,
   test TX (TCFM), config (UWBCFG), PHY counters (LSTAT), DIAG. Drives the
   Pi-side dashboard and the sniffer tools. `flash.sh cli`.

Flashing uses **OpenOCD** (not pyocd — it can't drive the J-Link OB).

## Layout

- `uwb_explorer/` — tested Python package: `transport` (serial framing),
  `parser` (CLI output → typed events), `mac` (802.15.4z decode), `device`
  (board control), `radar` (rolling model), `serialport` (VID discovery),
  `tui` (Textual dashboard), `console` (raw REPL), `web` (phone web dashboard),
  `webmodel` (the Geiger-counter state model), `ble` (BLE peripheral),
  `blecodec` (BLE wire format), `serve` (unified web+BLE service).
- `ios/` — the SwiftUI iPhone app (CoreBluetooth) — see `ios/README.md`.
- `tools/` — `flash.sh` (personality switch/backup), `detect.py` (UWB energy
  detector via LSTAT), `sniff.py` (frame sniffer + preamble sweep),
  `bytehunt.py` (raw-byte capture attempt), `dissect.py` (802.15.4z frame
  dissector), `con-setup.sh` (portable-unit autostart + WiFi hotspot).
- `firmware/` — `cli.hex` + factory backup (= the QANI/NI image).
- `artifacts/` — the shareable AirTag capture report (HTML + PNG).
- `docs/` — `FINDINGS.md` (results), `IDEAS.md` (next projects),
  `cli-protocol.md` (protocol reference), `EXPLORER.md`, `IPHONE.md`, `CON-KIT.md`.
- `tests/` — pytest suite (TDD). `PLAN.md` — full handoff log.

Not in git (download from Qorvo; see `.gitignore`): `fw-downloads/` (the 291 MB
official DK package), `docs/vendor/` (Qorvo PDFs + scripts), `firmware/*.hex`
and the factory `*.bin` backup, and `.fwbuild/` (extracted SDK for firmware work).

## Docs map

- New here? → this file, then `docs/FINDINGS.md`.
- Want the iPhone demo → `docs/IPHONE.md`.
- Build the native iOS (BLE) app → `ios/README.md`.
- Want the terminal explorer → `docs/EXPLORER.md`.
- Build the portable con device → `docs/CON-KIT.md`.
- CLI command/protocol details → `docs/cli-protocol.md`.
- What to build next → `docs/IDEAS.md`.
- Resuming the work / full history → `PLAN.md`.
