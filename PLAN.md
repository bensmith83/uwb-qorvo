# PLAN / HANDOFF — UWB Explorer (Qorvo DWM3001CDK)

> Living document. Any model/agent picking this up: read this top to bottom,
> then check "Current state" and continue from "Next steps".
> Keep this file updated as work proceeds.

## Mission (user's ask, 2026-07-07)

Turn the attached Qorvo DWM3001CDK into a cool device for **exploring UWB
tech**. Open-ended by design. Preferred directions, in user's priority order:

1. **iPhone interaction** — walk around with the board + iPhone, see UWB
   stuff react. (User prefers iPhone over Android right now.)
2. **Serial/terminal explorer** — board tethered to this Pi, terminal UI
   showing UWB things it sees/interacts with.
3. Anything else cool. User has spare boards available if needed
   (Pi Zero W, Nordic nRF boards, etc.) — ask if useful.

User expects us to "figure it out and knock it out of the park."

## Hardware facts (verified)

- Host: Raspberry Pi 4, 4GB, aarch64, Debian-ish, Python 3.13.5.
  **Constraint: max 2-3 parallel background agents (12 caused a lockup).**
- Board: DWM3001CDK = nRF52833 (BLE SoC) + Qorvo DW3110 UWB transceiver,
  integrated SEGGER J-Link OB.
- Board's J-Link USB port DID enumerate on this Pi as VID 1366 PID 0105,
  serial `000760201387`, giving `/dev/ttyACM0` (CDC-ACM bridged to nRF UART),
  then disconnected after 3 s (loose cable / unplugged). **As of writing,
  board is NOT connected — user asked to replug the J-Link micro-USB port.**
- The board has two micro-USB ports: J9 = J-Link OB (the one we want, also
  powers board), J20 = nRF52833 native USB (some firmware exposes CDC here).
- Also on the Pi's USB: a Google Pixel phone (charging) — ignore it.

## Toolchain (verified working on this Pi)

- `pyocd 0.42.0` at `/home/pi/.local/bin/pyocd`; has builtin `nrf52833`
  target; supports J-Link OB probes via libusb — no SEGGER software needed.
  Flash: `pyocd flash -t nrf52833 <file.hex>`
  Erase: `pyocd erase -t nrf52833 --chip`
- Project venv: `/home/pi/xfer/vibin/uwb-qorvo/venv` with pyserial, pytest,
  textual, rich. ALWAYS use `./venv/bin/python` — never system pip, never
  `--break-system-packages` (user hard rule).
- User hard rule: **red/green TDD** for the Python code we write.

## Architecture plan

Two firmware "personalities" for the board, switchable via pyocd:

### A. iPhone path — Qorvo Nearby Interaction (NI) firmware
- Qorvo ships NI accessory firmware for DWM3001CDK that interops with
  Apple's U1/U2 chip via the Nearby Interaction framework + BLE GATT
  (Apple NI accessory protocol).
- User installs the free **"Qorvo Nearby Interaction"** app from the iOS App
  Store → live distance (and direction if iPhone camera-assist available)
  to the board. Zero iOS development required.
- Deliverable: flashed firmware + docs/IPHONE.md walkthrough.

### B. Explorer path — Qorvo CLI firmware + Pi terminal dashboard
- Qorvo's CLI firmware (from the "DWM3001CDK DK" package) exposes a UART
  shell: FiRa TWR ranging (INITF/RESPF), and crucially **LISTENER mode**
  which dumps received UWB frames (RSSI/CIR/hex payload) — a UWB sniffer.
- We build `uwb_explorer/`: Python package (serial protocol driver, TDD'd)
  + Textual TUI dashboard: live frame feed, decoded FiRa/BLINK types,
  ranging sessions, signal strength.
- Stretch: decode 802.15.4z MAC headers of sniffed frames, log sessions to
  disk, map activity over time.

### Firmware acquisition (in progress)
- Background agent "fw-hunter" is downloading firmware images to
  `/tmp/claude-1000/-home-pi-xfer-vibin-uwb-qorvo/5d337146-7c51-40ff-908f-24ac2748f1de/scratchpad/fw-downloads/`
  (Qorvo official downloads may be behind free registration; agent also
  checking GitHub mirrors). Vendored copies + provenance go in `firmware/`.
- If downloads are login-walled: ask user to fetch from qorvo.com
  (DWM3001CDK product page → "DWM3001CDK DK Software, Sources, Tools and
  Developer Guide" and/or "Qorvo Nearby Interaction" package) — they have a
  browser; files land in `~/Downloads` or similar.

## KEY FINDINGS (2026-07-07)

- **pyocd can NOT see the J-Link OB** (needs SEGGER lib). **Use OpenOCD
  0.12 (apt-installed) instead** — works perfectly:
  `openocd -f interface/jlink.cfg -c "transport select swd" -f target/nrf52.cfg -c "init; halt; ...; exit"`
- udev rule added: /etc/udev/rules.d/99-jlink.rules (VID 1366 mode 0666).
- **Factory firmware = DWM3001CDK-QANI-FreeRTOS** (Qorvo Apple Nearby
  Interaction demo, FiRa stack, DW3XXX driver 06.00.14) — confirmed via
  strings on flash dump. Board advertises BLE as `DWM3001CDK (1613B863)`
  (MAC EC:3B:16:13:B8:63). **iPhone path works out of the box** with the
  "Qorvo Nearby Interaction" App Store app.
- Factory backup taken BEFORE any flashing:
  `firmware/factory-backup.bin` (512K, md5 acee86a12d34ca21c3c9c338d18d7de6)
  `firmware/factory-uicr.bin` (4K, md5 f2d4b6fb85d8df808202f500ce38966b)
  So `tools/flash.sh ni` == restore factory backup.
- QANI factory firmware is silent on the J-Link UART (ttyACM0) at all
  common bauds (its CLI, if any, may be on J20 native USB).
- Chip is readable (no APPROTECT lock). nRF52833-CJAA A0, 512K/128K.

## HARDWARE-VERIFIED FINDINGS (2026-07-07, board live on J20)

- **CLI console is on J20 native USB (Nordic VID 0x1915), confirmed in
  firmware source** (`board_interface_init()` → `Usb.init()`; J-Link UART
  only if `get_uartEn()`). Verified talking: STAT/UWBCFG/LISTENER2/STOP/
  SAVE all respond correctly, formats match parser.
- **PORTS RENUMBER on every replug.** J-Link and Nordic swap between
  /dev/ttyACM0 and ttyACM1 unpredictably. NEVER hardcode — always use
  `uwb_explorer.serialport.find_cli_port()` (selects by Nordic VID). The
  dashboard/console already do this.
- **This board reports "Found non-AOA DW3000"** at listener/ranging start →
  single-antenna variant: **distance works, angle-of-arrival (PDoA/AoA)
  does NOT** in CLI mode (LAoA/RAoA fields read ~0). Don't promise AoA on
  the CLI path. (iPhone NI can still show direction via the phone.)
- Native-USB CDC can re-enumerate (renumber) if hammered with rapid
  open/close or buffer floods → looks like a "wedge". Recover with a
  J-Link reset: `openocd -f interface/jlink.cfg -c "transport select swd"
  -f target/nordic/nrf52.cfg -c "init; reset run; exit"`. Board firmware
  itself never bricked.
- Radio TX healthy (TCFM ran). **Channels 5 and 9 both silent** with no
  active UWB nearby — expected (UWB doesn't beacon; only ranging sessions
  emit). Need a traffic source for a live sniff demo (see options below).
- Board currently: CLI fw, channel 9 SP0 (saved to NVM), STOP mode,
  responsive.
- Driver hardened: `CliSession.command(flush=True)` drains stale rx so a
  query right after LISTENER isn't polluted; Device uses it. 43 tests green.

## LIVE SNIFF HUNT — Apple AirTag/U1 (2026-07-07, in progress)

Goal: passively sniff the iPhone↔AirTag UWB ranging session (user confirmed
precision-finding engaged: arrow + distance shown → U1 IS transmitting on
ch9). Also user wants: (a) a shareable decoded-frame artifact for LinkedIn,
(b) confirmation we can sniff iPhone↔other-board sessions (yes — same
promiscuous mechanism).

Tools built (TDD, 47 tests green):
- tools/sniff.py — full-frame-dump listener + MAC decode + optional
  preamble sweep. `python tools/sniff.py --seconds N [--sweep] [--channel C]`
- tools/detect.py — the KEY instrument: polls LSTAT PHY counters (SFDD/PHE/
  CRCB/…) to detect UWB ENERGY even when frames are encrypted/undecodable.
  `python tools/detect.py --sweep --codes 10,11,12 --seconds 55`
- Device.get_lstat(), start_listener(full=True) added.

Findings so far:
- Board deaf on **channel 9, preamble code 9**: 2 runs (45s + 50s) with the
  AirTag arrow ACTIVE → ZERO on all counters (SFDD/PTO/SFDTO all 0). Zero
  preamble-acquisition events ⇒ **preamble code 9 is NOT Apple's code**
  (a code mismatch makes the correlator deaf; a mere SFD/STS mismatch would
  still tick SFDTO). → code 9 effectively ruled out.
- QANI firmware (factory backup) debug strings CONFIRM the PHY knobs that
  matter: channel_number, **preamble_code_index**, sfd_id, rframe_config,
  **sts_config (STS encryption)**, psdu_data_rate. Apple ranging = STS +
  ~SP3 (little/no plaintext payload) → don't expect a juicy decodable MAC
  payload; **SFDD (SFD detections) is the right "we heard it" signal**, plus
  RX timestamp + RSSI. Those still make a legit shareable capture.
- NEXT: coordinated sweep of preamble codes **10, 11, 12** on channel 9,
  AirTag arrow kept active the whole ~55s. If still nothing: sweep channel 5,
  then revisit PLEN/SFDTYPE. Command ready:
  `./venv/bin/python tools/detect.py --sweep --codes 10,11,12 --seconds 55 --interval 1.5`
- If we lock a code: switch to `tools/sniff.py --sweep`-found code with
  full dump to grab any frame bytes; build the shareable artifact.

## ★ MILESTONE: AirTag UWB DETECTED (2026-07-07) ★

Passive sniff SUCCEEDED. With AirTag precision-finding active ~30cm from
the board, sweeping preamble codes on channel 9:
- code 9  → 0 (silent; control)
- code 10 → PHE 555 (frame headers reached), CRCB 1, SFDTO 52
- code 11 → SFDTO 999 (preamble locks — correlator matched Apple's preamble)
- code 12 → PHE 209, SFDTO 252
Preamble-code selectivity = proof of real UWB (noise isn't code-selective).
Apple's frames are STS-encrypted (SP3) → NO decodable payload / no LSTN
frame dump possible (grab on code 10 full-dump returned 0 LSTN lines, only
PHE errors). So deliverable = detection evidence, not decoded bytes.
Raw logs: scratchpad/sweep2.log (the money data), grab10.log.

Shareable artifact BUILT + published:
artifacts/airtag-capture.html → https://claude.ai/code/artifact/65693c3b-635e-405a-bb0a-1a956ca56d3b
(instrument-style capture card, theme-aware, real numbers.)

Open threads / possible next steps:
- User wanted actual "frame bytes" for sharing. Apple STS blocks clean
  decode. Could chase with SFDTYPE/PHRMODE tuning but low odds. Honest
  framing given.
- To get a REAL decodable frame over-the-air we'd need a 2nd UWB
  transmitter (2nd DWM board). User's Nordic boards lack UWB. A 2nd
  DWM3001CDK would enable board→board TWR (INITF/RESPF) with clean decoded
  frames + live distance in the TUI — that's the natural "decoded frame"
  deliverable if they want one.
- Confirmed to user: iPhone↔other-board sniffing works by same mechanism.

## ★ PORTABLE CON DEVICE — phone web dashboard (2026-07-07) ★

User wants to take this to a con: battery-powered, "plug in and run." Clarified
the hardware: **UWB is only on the DWM3001CDK** (Thingy:52/:53 have no UWB radio
— dead ends). The board can't run the detector standalone, so the unit is
**DWM3001CDK + Pi Zero W + USB battery**, phone as screen over the Pi's own WiFi.
User confirmed: phone-web output only (no buzzer/screen), full con-ready
(autostart + hotspot), will run it on a Pi Zero W they own.

Built (TDD, red/green; 60 tests green total, +13):
- `uwb_explorer/webmodel.py` — `DetectorState`: pure Geiger-counter model.
  Folds LSTAT counter dicts → deltas, activity level (idle/low/medium/high),
  rolling sparkline history, peak, decoded count, status. `tests/test_webmodel.py`.
- `uwb_explorer/web.py` — stdlib `http.server` (no new deps, Pi-Zero-light).
  `DashboardServer` serves a self-contained phone page + `/api/state` JSON;
  `board_loop()` opens the CLI console, starts LISTENER, polls counters, with
  reconnect/retry so you can hot-plug the board. Optional `--sweep` cycles
  preamble codes 9-12. `poll_once()` seam tested with a fake device.
  `tests/test_web.py` (loopback server, no hardware).
- `tools/con-setup.sh` — one-time, run ON THE PI: installs a systemd service
  (autostart on boot, port 80, --sweep) + NetworkManager WiFi AP
  ("UWB-Explorer" / gateway 10.42.0.1). `--undo` reverts. DO NOT run on the dev
  Pi 4 — it would hijack networking.
- `docs/CON-KIT.md` — BOM, cabling, and the run book. `./run.sh web` added.

VERIFIED LIVE: `./run.sh web` came up **live** against the real board (read
CHAN 9 / TXCODE 9, listener polling, JSON served, page 200, 404s correct).
hits=0 only because no active UWB was near the antenna during the test.

Open threads:
- Confirm on hardware whether the board runs on **J20 power alone** or needs a
  2nd 5 V feed to J9 (noted in CON-KIT). The one thing to check before the con.
- `con-setup.sh` assumes NetworkManager (Bookworm). Original Pi Zero W on an
  older dhcpcd/hostapd image needs the manual AP path (script warns + explains).
- Next visual polish possible: show per-counter breakdown / RSSI on the page.

## ★ FIRMWARE BOOT HANG SOLVED (2026-07-07, session 3) ★

The Pi-built GCC CLI firmware now fully works: builds, flashes, boots, J20
console live, listener + config save verified on hardware. Root cause was the
linker script placing `.fconfig` inline in `.text` — the app page-erases the
flash page holding `__fconfig_start` on config save and destroyed its own
`.dw_drivers` table (BusFault in `uwb_init`). Fixed in
`firmware/gen_makefile.py` (vendor memory map: fconfig gets its own page at
0x1E000, code at 0x1F000). Full story + breadcrumb-debugging technique in
docs/FIRMWARE.md. **Board is currently running our GCC-built image**
(`0.1.1-260707`); vendor restore: `tools/flash.sh cli` or `tools/flash.sh ni`.
Next: SoftDevice S113 + BLE GATT service (see docs/FIRMWARE.md next steps).

## Current state (update as you go!)

- [x] Board connected: J-Link VID 1366 → /dev/ttyACM0; solid red LED
- [x] OpenOCD flashing path verified; pyocd abandoned
- [x] venv + deps (pyserial pytest textual rich)
- [x] Project skeleton: firmware/ tools/ uwb_explorer/ tests/ docs/
- [x] Factory firmware backed up + identified (QANI = NI personality)
- [x] Transport layer TDD'd (tests/test_transport.py, 7 green)
- [x] iPhone walkthrough → user told to install "Qorvo Nearby Interaction"
      app; docs/IPHONE.md
- [x] CLI firmware obtained & staged: `firmware/cli.hex`
      (= DWM3001CDK-DW3_QM33_SDK_CLI-FreeRTOS_0_1_1.hex, verified Intel HEX,
      from official Qorvo DK zip mirrored login-free at
      github.com/sasodoma/uwb-ranging releases; full 291MB DK package in
      `fw-downloads/`, UCI hexes there too). iPhone QANI hex is NOT
      publicly downloadable (CAPTCHA wall) — but we don't need it, factory
      backup IS QANI 3.x.
- [x] Official Developer Guide extracted: docs/vendor/*.pdf + guide.txt
      (pdftotext). CLI protocol documented there with verbatim samples:
      * ranging: {"Block":N, "results":[{"Addr":"0x0001","Status":"Ok",
        "D_cm":9,"LPDoA_deg":..,"LAoA_deg":..,"LFoM":0,"RAoA_deg":..,
        "CFO_ppm":..}]}
      * listener2 frames: JS00EF{"LSTN":[49,2B,..hex..],"TS":"0x..","O":N}
        (pseudo-JSON, unquoted hex!)  JSxxxx prefix = hex payload length.
      * info blocks: JS010F{"Info":{...}}, JS00BE{"UWB PARAM":{"CHAN":9,..}}
      * cmds: HELP STOP STAT SAVE THREAD DECA$ / LISTENER2 [1] LSTAT /
        INITF RESPF [params: rframe slot_rstu block_ms round_slots rr_usage
        session_id vupper64 multinode hopping init_addr resp_addr...] /
        TCFM TCWM / UWBCFG RESTORE DIAG TXPOWER ANTENNA DECAID VERSION /
        UART <n> selects console uart. Example: initf 4 2400 200 25 2 42
        01:02:03:04:05:06:07:08 0 0 0 1 ; responder same but respf.
- [x] CLI firmware FLASHED and running on the board now.
      **Finding: CLI console is ONLY on J20 (native USB CDC), NOT on the
      J-Link CDC (ttyACM0). ttyACM0 stays silent under CLI fw. Blind
      `UART 0/1` over ttyACM0 did nothing. User asked (11:10) to plug a
      2nd micro-USB cable into J20 (keep J9 too). Watcher b0joml540 waits
      for a second /dev/ttyACM*.** J20 will enumerate as a separate USB
      device (Nordic VID, likely /dev/ttyACM1).
- [x] Parser TDD'd: uwb_explorer/parser.py (RangingResult/ListenerFrame/
      InfoBlock/Ack) — 16 tests green total (tests/test_parser.py,
      test_transport.py).
- [x] iPhone NI test DONE by user: Qorvo NI app connected, live distance,
      dropped ~8.1 m through a wall (drop possibly caused by our reflash
      mid-test). There are 2 Qorvo apps (regular + background service one).
- [x] Extended parser for BOTH SDK output formats (CFO_ppm vs CFO_100ppm,
      TS vs TS4ns, rsl/fsl signal levels, bare-array compact ranging).
- [x] 802.15.4z MAC decoder (uwb_explorer/mac.py) — frame type, version,
      security, addresses; handles 2015 PAN-compression rules.
- [x] Device driver (uwb_explorer/device.py): detect()/stop()/
      start_listener()/start_ranging()/get_uwbcfg()/set_channel()/
      poll_events(); reassembles multi-line JSxxxx blocks.
- [x] RadarModel (uwb_explorer/radar.py): rolling contacts (active-range vs
      passive-sniff), distance history, frame/range counters.
- [x] Textual TUI (uwb_explorer/tui.py): contact table + raw event log +
      stats + distance sparkline; keys l/i/r/s/5/9/c/q. VERIFIED headless
      via Textual run_test with fake device (tests/test_tui.py).
- [x] Serial auto-discovery (uwb_explorer/serialport.py): prefers Nordic
      VID 0x1915 (J20), de-prioritises SEGGER VCOM.
- [x] Interactive console (uwb_explorer/console.py) + run.sh launcher +
      docs/EXPLORER.md + docs/IPHONE.md + requirements.txt.
- [x] **42 pytest tests green.** `./run.sh test`
- [ ] **BLOCKED ON HARDWARE: need J20 cable.** Confirmed (twice, incl. DTR
      toggle) the CLI console is silent on the J-Link VCOM (ttyACM0). It is
      only on J20 native USB. User asked to plug a 2nd micro-USB cable into
      J20 (keep J9 for flashing). Watcher b0joml540 polls for a 2nd
      /dev/ttyACM*. Until then the dashboard can't talk to real hardware —
      but the whole pipeline is validated against captured/spec formats.
- [ ] Once J20 up: `./run.sh dash`, hit `l` to sniff, confirm real
      LISTENER2 frames parse; walk around; then flash-ni to range w/ phone.

## Coolest-demo notes / honest limits (for whoever continues)

- We have ONE UWB board. The iPhone is effectively the 2nd UWB device, BUT
  it speaks Apple Nearby Interaction (pairs with QANI fw), not FiRa CLI
  ranging. So it's EITHER: CLI+LISTENER to sniff the environment, OR QANI
  to range with the phone — one firmware at a time (swap via tools/flash.sh
  in seconds).
- LISTENER only fully decodes frames matching its UWBCFG PHY (channel 5/9,
  preamble code, SFD, STS). Commercial UWB (car keys, AirTags, other
  phones) may use different channel/encrypted STS → may show as partial
  headers / energy, not clean packets. The 5/9 channel keys let you sweep.
- If user wants richer multi-device ranging: a 2nd DWM3001CDK (or any FiRa
  board) is the unlock. User said they have spare boards — a 2nd DWM would
  let INITF/RESPF run board-to-board with our dashboard on both ends.
- Alt UCI firmware (firmware present in fw-downloads) + Qorvo Python is the
  path if we ever want host-driven ranging over the J-Link port instead.

## Gotchas / notes

- Take a full flash backup BEFORE erasing — board ships with factory
  firmware we may want back.
- nRF52833 may have APPROTECT enabled → pyocd may need
  `--config` auto-unlock or `pyocd erase --chip` w/ recover; if flash fails
  try `pyocd cmd -t nrf52833 -c reset` then mass erase.
- CLI firmware historically talks 115200 8N1 on the J-Link CDC-ACM port;
  some builds use the J20 native USB CDC instead — if no shell on ttyACM0,
  ask user to also/instead connect J20.
- Serial console tip: user can run interactive commands themselves with
  `! <command>` in the Claude prompt.
- When session ends with real results: offer kb-capture (user's standing
  instruction).
