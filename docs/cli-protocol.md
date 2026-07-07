# DWM3001CDK "CLI firmware" — UART protocol reference

Research notes for the Qorvo DWM3001CDK **CLI** firmware personality (the
`DWM3001CDK-CLI-FreeRTOS.hex` / `DWM3001CDK-DW3_QM33_SDK_CLI-FreeRTOS.hex`
build shipped in the *DW3xxx & QM33 SDK* a.k.a. "DWM3001CDK DK Software,
Sources, Tools and Developer Guide" package).

Everything below is sourced from (a) the actual CLI firmware C source, mirrored
on GitHub in the Uberi cleaned-up fork, and (b) verbatim serial captures posted
on the Qorvo tech forum. Source URLs are cited inline. **Where a sample line is
quoted, it is copied verbatim from a real device capture** — those are the most
trustworthy part of this document.

> **Biggest gotcha up front:** the ranging **output format changed between SDK
> 1.0.2 and SDK 1.1.x**. 1.0.2 (and the Uberi fork) emit a compact
> `{"Block":..,"results":[{"Addr":..,"D_cm":..}]}` JSON. 1.1.0/1.1.1 emit
> verbose FiRa-UCI-style text: `FiRa Session Parameters {…}`,
> `SESSION_STATUS_NTF {…}`, `SESSION_INFO_NTF {…}`. If the board has FW 1.1.0
> (build Aug 2025) it will produce the **`SESSION_INFO_NTF`** form, not `D_cm`.
> See §5. Plan your parser for both.

---

## 0. Firmware personalities (what to flash / what ships)

The DWM3001CDK does **not ship pre-programmed** — Qorvo's guidance is that you
must flash a firmware yourself (blank/needs-flashing out of the box).
[[forum 21831]](https://forum.qorvo.com/t/dwm3001cdk-uci-firmware-com-not-working/21831)
The SDK package ships three prebuilt binaries under `SDK/Binaries/DWM3001CDK/`:

| Binary | Interface | Use |
|---|---|---|
| `DWM3001CDK-CLI-FreeRTOS.hex` | Human-readable **CLI** over UART/USB | **This document.** Terminal driving: INITF/RESPF/LISTENER. |
| `DWM3001CDK-UCI-FreeRTOS.hex` | Binary **UCI** (UWB Command Interface) over UART | Machine control; Qorvo ships Python UCI scripts. Different protocol entirely. |
| `DWM3001CDK-QANI-…full_QNI…hex` | Qorvo **Nearby Interaction** (BLE + UWB) | Apple/Android NI phone interop. Not a serial CLI. |

Sources: [SDK v1.1.1 layout / erase-before-flash](https://forum.qorvo.com/t/fira-applications-initf-in-dw3-qm33-sdk-1-1-0-and-1-1-1/24933),
[Getting started thread](https://forum.qorvo.com/t/getting-started-with-the-dwm3001c/13430).

---

## 1. Serial parameters & which port

- **Port: the nRF52833 *native USB* — connector J20 ("User USB", the upper
  micro-USB).** Qorvo's own instruction: *"connect a flashed DWM3001CDK to a PC
  using the User USB (J20), the PC will find a new COM Port … try the command
  'help'."*
  [[Getting started #5]](https://forum.qorvo.com/t/getting-started-with-the-dwm3001c/13430)
  The Uberi fork's README says the same (connect **J20**, the nRF's USB port).
  It enumerates as a **CDC-ACM** device with **Nordic VID `0x1915`** (the Uberi
  build uses PID `0x520F`; stock Qorvo build uses a Qorvo/Nordic PID — match by
  the *new* COM/ttyACM that appears when you plug J20).
  - The J9 / J-Link (lower micro-USB) port is for **flashing + RTT debug log
    only**, not the CLI data path. (This corrects the project PLAN's assumption
    that the CLI is on the J-Link VCOM — for the QM33 CLI it is the nRF native
    USB.)
  - There is also a UART on the Raspberry-Pi header, selectable with `UART 1` +
    `SAVE`, if you want a raw UART instead of USB.
    [[13446]](https://forum.qorvo.com/t/dwm30001cdk-standalone-operation-without-connection-to-usb-uart/13446)
- **Baud / framing:** because J20 is *native USB CDC*, the baud rate is nominal
  (USB ignores it) — any setting works. If you use the J-Link VCOM / hardware
  UART path instead, it is **115200 8N1, no flow control**.
- **Line ending:** a command is dispatched when the firmware sees **CR or LF**
  (`\r` or `\n`). Either works; sending `\r\n` is fine.
  (`usb_uart_rx.c`: it terminates the buffer on `'\n' || '\r'`.)
- **Prompt:** there is **no shell prompt** (no `>`). It is line-oriented: you
  send a line, it prints the response. The firmware does not echo a prompt back.
- **Case-insensitive:** input is upper-cased before matching, so `initf` ==
  `INITF`. (`cmd.c`: `toupper()` over the whole line.)
- **Multiple commands per line:** you may pack several commands separated by
  `\n`; they execute in sequence. (`cmd.c` `strtok(text,"\n")`.)
- **Two input syntaxes:** plain text (`INITF 4 2400 …`) **or** a JSON object
  `{"INITF":{…params…}}`. The parser tries JSON if the line starts with `{`.
- **Generic replies:** a command that succeeds returns `ok\r\n`
  (`CMD_FN_RET_OK`); a failure returns `error \r\n` optionally followed by a
  reason such as `error  incompatible mode` or `error  function`
  (`cmd.c cmd_onERROR`). Config/JSON commands prefix machine output with a
  `JSxxxx` length header — see §6.

Source files (verbatim, Uberi mirror of the CLI SDK):
[`Src/Apps/cmd/cmd.c`](https://github.com/Uberi/DWM3001CDK-demo-firmware/blob/main/Src/Apps/cmd/cmd.c),
[`Src/Apps/usb_uart_rx.c`](https://github.com/Uberi/DWM3001CDK-demo-firmware/blob/main/Src/Apps/usb_uart_rx.c),
[fork README](https://github.com/Uberi/DWM3001CDK-demo-firmware/blob/main/README.md).

---

## 2. Command table (the "anytime" + service commands)

Taken verbatim from the CLI command-registration tables in
[`Src/Apps/cmd/cmd_fn.c`](https://github.com/Uberi/DWM3001CDK-demo-firmware/blob/main/Src/Apps/cmd/cmd_fn.c)
(`known_commands_anytime_all[]`, `known_commands_idle_uart[]`,
`known_commands_service_all[]`) and the FiRa app table in
[`Src/Apps/fira_fn.c`](https://github.com/Uberi/DWM3001CDK-demo-firmware/blob/main/Src/Apps/fira_fn.c).

### Anytime commands (work in any mode)

| Command | Function | Notes (comment string from source) |
|---|---|---|
| `HELP` / `?` | `f_help_app` | Lists all commands. `HELP <CMD>` prints help for one command, e.g. `HELP SAVE`. |
| `STOP` | `f_stop` | **Stops the running top-level app** and returns to IDLE. This is how you stop INITF/RESPF/LISTENER. |
| `THREAD` | `f_thread` | Displays heap and thread stack usage. |
| `STAT` | `f_stat` | Displays status: current MODE/app name, last error code, then a `DECA$`-style JSON info block. |
| `SAVE` | `f_save` | Saves current app + UWB config to NVM, so it auto-starts on next power-up. |
| `DECA$` | `f_decaJuniper` | Reports running app + version + app list + driver/stack version as a `JSxxxx{…}` JSON object. |

### IDLE-only service / config commands (must `STOP` to IDLE first)

| Command | Function | Notes |
|---|---|---|
| `UART <0\|1>` | `f_uart` | Enable/disable the hardware UART (RPi header). |
| `RESTORE` | `f_restore` | Restore default UWB + system configuration. |
| `DIAG <0\|1>` | `f_diag` | Diagnostic mode: adds RSSI/CFO/NLOS complementary info to ranging output. |
| `UWBCFG [params…]` | `f_uwbcfg` | Show/set the low-level UWB PHY config (channel, PLEN, PAC, TX/RX code, SFD, datarate, PHR, STS, PDoA…). 13 params. |
| `STSKEYIV [key iv mode]` | `f_stskeyiv` | Show/set STS Key, IV and static/dynamic mode. |
| `TXPOWER [pwr pgdly pgcount]` | `f_power` | Show/set TX power registers. |
| `ANTENNA [types…]` | `f_antenna` | Show/set antenna type per port. `ANTENNA VALUES` lists allowed names. |
| `DECAID` | `f_decaid` | Prints UWB chip device/lot/part IDs. |
| `VERSION` | `f_get_version` | Prints `VERSION:<full version>`. |
| `PAVRG [n]` | `f_pdoa_average` | PDoA phase-difference averaging window. |

### Application-launch commands

| Command | Function | Notes |
|---|---|---|
| `INITF …` | `f_initiator_f` | Start FiRa TWR **Initiator/Controller**. See §3. |
| `RESPF …` | `f_responder_f` | Start FiRa TWR **Responder/Controlee**. See §3. |
| `LISTENER` | (listener app) | Promiscuous UWB frame sniffer. See §4. *Present in the full Qorvo CLI build; the Uberi fork removed it, TCFM, TCWM and USB2SPI to simplify — see §7.* |

> **`SETAPP <APP>`** — older SDK builds used a two-step `SETAPP INITF` then
> `SAVE` to select the auto-start app. In current builds you just type the app
> name (`INITF`) directly, and `SAVE` while it runs.
> [[13430 #5]](https://forum.qorvo.com/t/getting-started-with-the-dwm3001c/13430),
> [[22008]](https://forum.qorvo.com/t/how-to-hardcode-application-startup-initf-listener-respf-in-dwm3001cdk-firmware/22008)

The full command list is also printed live by the `HELP` command
(grouped: "Anytime commands", "Application selection", "IDLE time commands",
"Service commands").

---

## 3. FiRa two-way ranging: INITF / RESPF

Two argument syntaxes exist depending on SDK version.

### 3a. Positional syntax (SDK ≤1.0.2 / Uberi fork)

From the source help strings in `fira_fn.c` (verbatim):

```
INITF [RFRAME BPRF set] [Slot duration rstu] [Block duration ms] [Round duration slots] [RR usage] [Session id] [vupper64 xx:xx:xx:xx:xx:xx:xx:xx] [Multi node mode] [Round hopping] [Initiator Addr] [Responder 1 Addr] ... [Responder n Addr]
RESPF [RFRAME BPRF set] [Slot duration rstu] [Block duration ms] [Round duration slots] [RR usage] [Session id] [vupper64 xx:xx:xx:xx:xx:xx:xx:xx] [Multi node mode] [Round hopping] [Initiator Addr] [Responder Addr]
```

Worked example (Qorvo forum, 1 initiator + 2 responders, multi-node):
[[17215 #8]](https://forum.qorvo.com/t/dwm3001cdk-responder-implementation/17215)

```
Initiator:   INITF 4 2400 200 25 2 42 01:02:03:04:05:06:07:08 1 0 0 1 2
Responder1:  RESPF 4 2400 200 25 2 42 01:02:03:04:05:06:07:08 1 0 0 1
Responder2:  RESPF 4 2400 200 25 2 42 01:02:03:04:05:06:07:08 1 0 0 2
```

Reading the initiator line: RFRAME/BPRF set `4`, slot `2400` rstu, block `200`
ms, round `25` slots, RR-usage `2` (= DS-TWR), session `42`, vupper64
`01:…:08`, multi-node-mode `1`, hopping `0`, initiator addr `0`, responder
addrs `1` and `2`. (Channel is **not** in this positional list — it comes from
`UWBCFG`; default channel is 9, see §8.)

### 3b. Named-argument syntax (SDK 1.1.x)

Each argument is `-KEY=VALUE` and **must be preceded by a `-`**.
[[24165 #2]](https://forum.qorvo.com/t/qorvo-dwm3001cdk-cli-commands/24165)

```
INITF -CHAN=9 -PRFSET=BPRF4 -PCODE=10 -SLOT=2400 -BLOCK=200 -ROUND=25 -RRU=DSTWR -ID=42 -VUPPER=4F:86:82:A1:9A:9C:1F:26 -ADDR=0 -PADDR=1
```

Keys: `-CHAN` channel, `-PRFSET` (e.g. `BPRF4`), `-PCODE` preamble code,
`-SLOT` slot duration rstu, `-BLOCK` block duration ms, `-ROUND` round duration
slots, `-RRU` ranging-round usage (`DSTWR`/`SSTWR`), `-ID` session id,
`-VUPPER` vUpper64 STS seed (8 bytes `xx:..:xx`), `-ADDR` this device's short
addr, `-PADDR` peer short addr.

### 3c. Parameters that MUST match between the two boards

The two devices only range if these agree: **channel, PRF/preamble code, slot &
block & round durations, RR-usage, session id, vUpper64, multi-node mode**, and
the addresses must be paired (initiator's `-ADDR` == responder's peer, etc.).
On INITF the firmware echoes the whole resolved parameter set (see §5) so you
can diff the two boards.

### 3d. Defaults (compiled in, `fira_default_params.h`)

```
RFRAME_CONFIG   = SP3            SFD_ID          = 2
SLOT_DURATION   = 2400 rstu      BLOCK_DURATION  = 200 ms
ROUND_DURATION  = 25 slots       RR_USAGE        = DS-TWR
SESSION_ID      = 42             MULTI_NODE_MODE = UNICAST
ROUND_HOPPING   = false
CONTROLLER short addr = 0x0      CONTROLEE short addr = 0x1
CHANNEL (via UWBCFG/-CHAN)       = 9   (NOT 5)
```

---

## 4. LISTENER (promiscuous UWB sniffer) — the interesting one

`LISTENER` is a Qorvo example app on the CLI framework that puts the DW3110 into
promiscuous RX and prints **every received UWB frame that matches the configured
PHY** (channel, preamble, etc.), as a JSON object with the raw payload bytes plus
per-frame RF metadata. It does no decoding — you decode the 802.15.4z bytes
yourself; parts of the FiRa payload are STS-encrypted.
[[23549 #3, BKqorvo]](https://forum.qorvo.com/t/how-to-check-distance-data-in-the-cli-of-dwm3001cdk/23549)

**Verbatim listener output line** (real capture,
[[23549 #1]](https://forum.qorvo.com/t/how-to-check-distance-data-in-the-cli-of-dwm3001cdk/23549)):

```
JS00D7{"LSTN":[49,2B,00,00,26,13,00,FF,18,5A,08,08,08,08,08,08,08,08,2A,00,00,00,CA,A6,C2,57,00,3F,D9,10,C9,20,6A,17,C4,3C,58,95,ED,91,DA,CA,52,57,D9,A3,8C,A7,27],"TS4ns":"0x47F2D4D8","O":1224,"rsl":-64.71,"fsl":-64.95}
```

Field breakdown:

| Field | Meaning |
|---|---|
| `JS00D7` | `JS` + 4 hex digits = **byte length of the JSON that follows** (a Qorvo framing header on machine-JSON output; here 0x00D7 = 215). Strip/parse it before JSON-decoding. See §6. |
| `LSTN` | Array of received frame bytes, **decimal-in-hex** notation (each element is a hex byte written without `0x`). The **last 2 bytes are the CRC16** (auto-computed by the DW3110 IC per IEEE 802.15.4z §5.3). [[18039]](https://forum.qorvo.com/t/dwm3001cdk-tcfm-listener-configuration/18039) |
| `TS4ns` | RX timestamp, hex, in units of ~4 ns (device time counter). |
| `O` | Clock/carrier offset integer (integrator offset). |
| `rsl` | Received Signal Level of the whole packet, dBm. |
| `fsl` | First-path Signal Level, dBm (rsl−fsl gap hints at LOS/NLOS). |

In a normal FiRa DS-TWR (one-to-many, deferred) exchange with 4 anchors + 1 tag,
a listener will see this set of frames per round
[[23549 #3]](https://forum.qorvo.com/t/how-to-check-distance-data-in-the-cli-of-dwm3001cdk/23549):
1× RCM (Ranging Control Msg), 1× RIM (Ranging Initiation Msg), 4× RRM (Ranging
Response Msg), 1× RFM (Ranging Final Msg), 4× MRM (Measurement Report Msg),
1× RRRM (Ranging Result Report Msg).

**Start:** `LISTENER`  •  **Stop:** `STOP` (or reset).  It also honors the PHY
set by `UWBCFG` — set the same channel/preamble as the network you want to
sniff, or you will see nothing.

---

## 5. Ranging output format (initiator/responder result reports)

### 5a. SDK 1.1.0 / 1.1.1 — verbose `*_NTF` text (LIKELY what a 2025 board prints)

On `INITF`, the firmware first echoes the resolved session parameters, then
`ok`, then a stream of status and info notifications. **Verbatim capture**
[[24933]](https://forum.qorvo.com/t/fira-applications-initf-in-dw3-qm33-sdk-1-1-0-and-1-1-1/24933):

```
FiRa Session Parameters: {
SESSION_ID: 42,
CHANNEL_NUMBER: 9,
DEVICE_ROLE: INITIATOR,
RANGING_ROUND_USAGE: DS_TWR_DEFERRED,
SLOT_DURATION [rstu]: 2400,
RANGING_DURATION [ms]: 200,
SLOTS_PER_RR: 25,
MULTI_NODE_MODE: UNICAST,
HOPPING_MODE: Disabled,
RFRAME_CONFIG: SP3,
SFD_ID: 2,
PREAMBLE_CODE_INDEX: 10,
STATIC_STS_IV: "01:02:03:04:05:06",
VENDOR_ID: "07:08",
DEVICE_MAC_ADDRESS: 0x0000,
DST_MAC_ADDRESS[0]: 0x0001
}
ok
SESSION_STATUS_NTF: {state="INIT", reason="State change with session management commands"}
SESSION_STATUS_NTF: {state="IDLE", reason="State change with session management commands"}
SESSION_STATUS_NTF: {state="ACTIVE", reason="State change with session management commands"}
SESSION_INFO_NTF: {session_handle=1, sequence_number=0, block_index=0, n_measurements=1
 [mac_address=0x0001, status="TX_FAILED"]}
```

**`SESSION_INFO_NTF` grammar** (from `fira_session_info_ntf_twr_cb()` in
`Src/Apps/Src/fira/fira_app.c`, documented on the forum
[[22984 #2]](https://forum.qorvo.com/t/is-there-any-documentation-for-the-format-of-the-initf-respf-log-message/22984)):

```
SESSION_INFO_NTF: {session_handle=<u32>, sequence_number=<u32>, block_index=<u32>, n_measurements=<int>
[mac_address=0x<hex>, status="<STATUS>"<conditional fields>]<;next measurement>
}
```

- Always present: `session_handle`, `sequence_number`, `block_index`,
  `n_measurements`, and per measurement `mac_address`, `status`.
- **`{}` bounds one notification; `[]` bounds one anchor's measurement;
  multiple measurements are separated by `;`.**
- `status` ∈ `SUCCESS`, `TX_FAILED`, `RX_TIMEOUT`, `RX_PHY_DEC_FAILED`,
  `RX_PHY_TOA_FAILED`, `RX_PHY_STS_FAILED`, `RX_MAC_DEC_FAILED`,
  `RX_MAC_IE_DEC_FAILED`, `RX_MAC_IE_MISSING`, `Unknown`.
- **Only when `status="SUCCESS"`**: `distance[cm]=<int>` always; then optionally
  `loc_az_pdoa`, `loc_az` (if `aoa_fom>0`), `loc_el_pdoa`, `loc_el`,
  `rmt_az`, `rmt_el`, and `RSSI[dBm]=<float>` (if rssi≠0). **Single-antenna
  boards (DWM3001C) never emit the AOA fields.**
- Conversions: angles `deg = 360·q16/65536`; RSSI `dBm = -1·q7/2`.

Verbatim example lines [[22984 #2]](https://forum.qorvo.com/t/is-there-any-documentation-for-the-format-of-the-initf-respf-log-message/22984):

```
SESSION_INFO_NTF: {session_handle=42, sequence_number=123, block_index=5, n_measurements=1
[mac_address=0x1234, status="SUCCESS", distance[cm]=150, RSSI[dBm]=-45.5]
}

SESSION_INFO_NTF: {session_handle=42, sequence_number=125, block_index=5, n_measurements=1
[mac_address=0x1234, status="SUCCESS", distance[cm]=150, loc_az_pdoa=25.30, loc_az=27.45, loc_el_pdoa=-5.20, loc_el=-3.10, RSSI[dBm]=-45.5]
}
```

With `DIAG 1`, extra `RANGE_DIAGNOSTICS_NTF: {n_reports=… [msg_id=…, action=TX/RX,
antenna_set=…, frame_status={SUCCESS:1,…}, cfo_ppm=…, nb_aoa=… tdoa=… pdoa=…
aoa=… fom=… type=…]}` blocks appear.

### 5b. SDK 1.0.2 / Uberi fork — compact `D_cm` JSON

Older builds' `report_cb()` (in `fira_app.c`) emits one compact JSON per block:

```
{"Block":<n>, "results":[{"Addr":"0x0001","Status":"Ok","D_cm":123,"LPDoA_deg":45.00,"LAoA_deg":30.00,"LFoM":100,"RAoA_deg":12.00,"CFO_100ppm":-5}]}
```

**Verbatim capture** (two DWM3001C at 61 cm,
[[13430 #6]](https://forum.qorvo.com/t/getting-started-with-the-dwm3001c/13430)):

```
[{"Addr":"0x0000","Status":"Ok","D_cm":61,"LPDoA_deg":0.00,"LAoA_deg":0.00,"LFoM":0,"RAoA_deg":0.00,"CFO_100ppm":-639}]
```

Fields (from source): `Addr` short MAC; `Status` `Ok`/`Err`; `D_cm` distance cm
(= `distance_mm/10`); `LPDoA_deg` local PDoA; `LAoA_deg` local AoA;
`LFoM` AoA figure-of-merit; `RAoA_deg` remote AoA azimuth; `CFO_100ppm` carrier
freq offset. Session end prints `{"Session Stopped":"Stop request"}`.
(AoA fields are 0 on single-antenna boards.) Source:
[`fira_app.c report_cb`](https://github.com/Uberi/DWM3001CDK-demo-firmware/blob/main/Src/Apps/fira_app.c).

---

## 6. The `JSxxxx` framing header (machine-JSON commands)

Config/report commands that return JSON prefix it with **`JS` + 4 uppercase hex
digits = the length of the JSON body**, then the `{…}` object, then `\r\n`.
Seen on `DECA$`, `UWBCFG`, `TXPOWER`, `STSKEYIV`, `PAVRG`, and `LSTN`.
Implementation (`cmd_fn.c`): `sprintf(str,"JS%04X",…)` reserves the header, the
body is built, then the real length is back-patched into those 4 hex digits.
Host parsers should read `JS`, take the next 4 hex chars as a length, then read
that many bytes of JSON. Plain human commands (`STAT`, `VERSION`, `HELP`,
ranging `SESSION_INFO_NTF`) do **not** use this header.

Example `DECA$` object shape:
`JSxxxx{"Info":{"Device":"DWM3001CDK - DW3_QM33_SDK - FreeRTOS","Current App":"…","Version":"…","Build":"…","Apps":["INITF","RESPF",…],"Driver":"…","UWB stack":"…"}}`.

---

## 7. Stopping, saving, autostart & standalone

- **Stop a running app:** send `STOP` → returns to IDLE and prints `ok`.
  (No magic key; it's the `STOP` command.) Then IDLE-only commands (UWBCFG etc.)
  become allowed again.
- **Persist / autostart:** run the app you want (`INITF`/`RESPF`/`LISTENER`),
  then `SAVE` **while it is running**. On next power-up it auto-starts that app
  with the saved UWB config. `RESTORE` reverts to defaults.
  [[17215 #2]](https://forum.qorvo.com/t/dwm3001cdk-responder-implementation/17215)
- **Standalone (no USB host):** a saved board will re-start ranging on
  bus/wall power, but older firmware could **hang in the report path when USB is
  unplugged** (it blocks on `reporter_instance.print`). Qorvo's fix is a
  high-frequency-clock request in `peripherals_init()` and/or removing the report
  print; or set `DEFAULT_APP = helpers_app_fira` and call
  `scan_fira_params("initf 2 2400 200 25 2 42", true)` in `main()`.
  [[13446 #6]](https://forum.qorvo.com/t/dwm30001cdk-standalone-operation-without-connection-to-usb-uart/13446),
  [[13430 #9]](https://forum.qorvo.com/t/getting-started-with-the-dwm3001c/13430)

---

## 8. Gotchas & defaults checklist

- **Erase the whole chip before flashing the CLI hex.** A leftover state /
  missing calibration makes INITF report perpetual `TX_FAILED` / `RX_TIMEOUT`.
  Qorvo's fix: full erase, or flash UCI first + run `load_cal` (calibration
  files in `SDK/Tools/uwb-qorvo-tools/scripts/device/load_cal/calib_files/DWM3001CDK`),
  then flash CLI **without** erasing so calibration survives.
  [[24933]](https://forum.qorvo.com/t/fira-applications-initf-in-dw3-qm33-sdk-1-1-0-and-1-1-1/24933)
- **Default channel is 9, not 5.** (Legal channels 5 or 9.) Set via `UWBCFG` or
  `-CHAN`. Both boards must match.
- **Default session id 42**, controller addr `0x0`, controlee addr `0x1`,
  vUpper64 seed `01:02:03:04:05:06:07:08` (STATIC_STS_IV `01..06` + VENDOR_ID
  `07:08`). Both boards must share session id + vUpper64 or they won't decrypt
  each other's STS.
- **Multi-node mode must match** on both ends (unicast vs one-to-many); for
  1 initiator + N responders set multi-node = 1 and give each responder a
  distinct `-PADDR`/addr, listed on the initiator line.
- **Single-antenna DWM3001C → no angle data.** `LPDoA`/`LAoA`/`loc_az…` are
  always 0 / absent; you need a 2-antenna part (e.g. QM33120WDK) for AoA.
  [[13430 #7]](https://forum.qorvo.com/t/getting-started-with-the-dwm3001c/13430)
- **Output format depends on SDK version** (see §5). Detect at runtime: if you
  see `SESSION_INFO_NTF` you're on 1.1.x; if you see `"D_cm"` you're on 1.0.2 /
  the Uberi fork.
- **Developer Manual** has a legend for the log fields on ~p.41; the UWB user
  manual (da008154) documents the CRC16 (§5.3).

---

## 9. Source code map (where the protocol lives)

Uberi's fork mirrors the Qorvo CLI SDK sources (heavily cleaned up but the
command/format code is faithful — note it **removed** LISTENER/TCFM/TCWM/USB2SPI
to simplify, so use it for INITF/RESPF/output format, and the stock Qorvo SDK zip
for the listener source):

- **Command table & generic commands:**
  [`Src/Apps/cmd/cmd_fn.c`](https://github.com/Uberi/DWM3001CDK-demo-firmware/blob/main/Src/Apps/cmd/cmd_fn.c)
  — `known_commands_anytime_all[]`, `known_commands_service_all[]`, the
  `HELP`/`STAT`/`SAVE`/`STOP`/`DECA$`/`UWBCFG`/… implementations and their
  `COMMENT_*` help strings.
- **Command parser / line handling:**
  [`Src/Apps/cmd/cmd.c`](https://github.com/Uberi/DWM3001CDK-demo-firmware/blob/main/Src/Apps/cmd/cmd.c),
  [`Src/Apps/usb_uart_rx.c`](https://github.com/Uberi/DWM3001CDK-demo-firmware/blob/main/Src/Apps/usb_uart_rx.c).
- **INITF/RESPF command registration & help text:**
  [`Src/Apps/fira_fn.c`](https://github.com/Uberi/DWM3001CDK-demo-firmware/blob/main/Src/Apps/fira_fn.c).
- **Ranging result formatting (compact `D_cm` form):**
  [`Src/Apps/fira_app.c`](https://github.com/Uberi/DWM3001CDK-demo-firmware/blob/main/Src/Apps/fira_app.c)
  `report_cb()`. In the stock 1.1.x SDK the equivalent is
  `fira_session_info_ntf_twr_cb()` in `Src/Apps/Src/fira/fira_app.c` (the
  `SESSION_INFO_NTF` form).
- **Output transport:**
  [`Src/Apps/reporter.c`](https://github.com/Uberi/DWM3001CDK-demo-firmware/blob/main/Src/Apps/reporter.c)
  (`reporter_instance.print` → USB CDC).
- **Defaults:**
  [`Src/Apps/fira_default_params.h`](https://github.com/Uberi/DWM3001CDK-demo-firmware/blob/main/Src/Apps/fira_default_params.h).
- **LISTENER source:** not in the Uberi fork. It lives in the stock SDK under
  `Src/Apps/Src/…listener…` — obtain the "DWM3001CDK DK Software, Sources, Tools
  and Developer Guide" zip from the
  [Qorvo product page](https://www.qorvo.com/products/p/DWM3001CDK) (free
  registration) to read `fira_app.c`/listener formatting directly.

## 10. Source URLs (all cited above)

- Uberi cleaned-up CLI firmware mirror: https://github.com/Uberi/DWM3001CDK-demo-firmware
- CLI commands (named `-KEY=` syntax): https://forum.qorvo.com/t/qorvo-dwm3001cdk-cli-commands/24165
- INITF output & erase-before-flash (SDK 1.1.x): https://forum.qorvo.com/t/fira-applications-initf-in-dw3-qm33-sdk-1-1-0-and-1-1-1/24933
- SESSION_INFO_NTF format spec: https://forum.qorvo.com/t/is-there-any-documentation-for-the-format-of-the-initf-respf-log-message/22984
- Getting started (port J20, `D_cm` sample, standalone): https://forum.qorvo.com/t/getting-started-with-the-dwm3001c/13430
- Responder multi-node example / SAVE: https://forum.qorvo.com/t/dwm3001cdk-responder-implementation/17215
- LISTENER `LSTN` output & frame types: https://forum.qorvo.com/t/how-to-check-distance-data-in-the-cli-of-dwm3001cdk/23549
- Listener/TCFM CRC16 note: https://forum.qorvo.com/t/dwm3001cdk-tcfm-listener-configuration/18039
- Standalone / report-hang fix: https://forum.qorvo.com/t/dwm30001cdk-standalone-operation-without-connection-to-usb-uart/13446
- Autostart (SETAPP/SAVE): https://forum.qorvo.com/t/how-to-hardcode-application-startup-initf-listener-respf-in-dwm3001cdk-firmware/22008
</content>
