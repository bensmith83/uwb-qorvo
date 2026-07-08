# Board-only firmware — plan & findings

Goal: custom **nRF52833 firmware** so the DWM3001CDK runs the UWB listener **and**
its own BLE service — board + battery + phone, no Pi. The iOS app (`ios/`) and BLE
wire format (`uwb_explorer/blecodec.py`) carry over; the board just becomes the
BLE peripheral instead of the Pi.

## What we know (verified 2026-07-07)

- **Toolchain installed:** `arm-none-eabi-gcc 14.2.1` on the Pi 4.
- **SDK:** Qorvo `DW3_QM33_SDK-FreeRTOS 0.1.1`, extracted to `.fwbuild/`
  (gitignored). Bundles **Nordic nRF5 SDK 17.1.0** (`SDK_BSP/Nordic/NORDIC_SDK_17_1_0`).
- **App project:** `Projects/DW3_QM33_SDK/FreeRTOS/DWM3001CDK/ses/` — CLI and UCI
  variants, **SES `.emProject` only** (no GCC Makefile for the app). Target
  `nRF52833_xxAA`; linker via SES `flash_placement.xml`.
- **CLI build:** 149 source files. Defines include `CLI_BUILD`, `USB_ENABLE`,
  `UWBSTACK`, `NRF52833_XXAA`. **No SoftDevice/BLE** in the CLI image (0 refs) —
  its console is the nRF native USB (J20).
- **BLE is available in the bundle:** `SDK_BSP/.../components/ble` + **16
  SoftDevice images** in the SDK. The factory **QANI** firmware already runs
  BLE + UWB together on this exact chip — proof the combo works.

## Build-system constraint (the fork)

The app only ships SES projects, and **`emBuild` (SES headless build) has no
ARM-host port**, so it **cannot run on this Pi 4** (aarch64). Two real paths:

1. **Build on the Mac (vendor path).** SEGGER Embedded Studio for ARM runs on
   macOS; open the `.emProject`, build with the free Nordic license. I write the
   firmware source; you build there. Flash via J-Link (SES or `openocd`).
   - Pro: uses the project exactly as shipped; least build-system risk.
   - Con: firmware build lives on the Mac, not this Pi.
2. **Hand-rolled GCC Makefile on the Pi.** Parse the `.emProject` (sources,
   includes, defines) into a Makefile, use the nRF5 SDK's GCC linker scripts +
   startup instead of SES's `flash_placement.xml`, build with the installed
   `arm-none-eabi-gcc`.
   - Pro: fully autonomous on the Pi; reproducible in CI.
   - Con: **high risk.** The 149 files include **2 prebuilt vendor `.a` libs**
     (UWB stack + DW3xxx driver, built with SES's toolchain — ABI/newlib
     mismatch risk with a different GCC), **SES-specific startup** (`thumb_crt0.s`
     from the SES install + `ses_startup_nrf52833.s`), and an SES
     `flash_placement.xml` to convert. Plus FreeRTOS + USB + mbedtls. Real
     chance of not converging.

**UPDATE (2026-07-07): the Pi GCC path WORKS — path 2 wins.** `firmware/gen_makefile.py`
parses the `.emProject` and emits a GCC Makefile + a merged linker script; the
**stock CLI firmware builds end-to-end on the Pi** (no SES, no Mac):

    python3 firmware/gen_makefile.py \
      .fwbuild/.../ses/DWM3001CDK-DW3_QM33_SDK_CLI-FreeRTOS.emProject .fwbuild/build-cli
    make -C .fwbuild/build-cli        # -> cli-firmware.hex

Result: 145 objects compile; ~321 KB flash / ~106 KB RAM; vectors @ 0x0, initial
SP @ 0x20020000; CLI command table and `.dw_drivers` table both populated. The
things that made it work:
- Swap SES startup (`ses_startup_*`, `thumb_crt0.s`) for the SDK's
  `gcc_startup_nrf52833.S` + `system_nrf52833.c`; link the **hard-float** prebuilt
  libs (`*-m4-hfp*`).
- Keep quoted `-D` values (e.g. `UWBMAC_BUF_PLATFORM_H="..."`).
- Translate SES `flash_placement.xml` custom tables (`.known_commands_*`,
  `.dw_drivers`, `.config_entry`, `.rconfig`) into the linker script by **merging**
  into a copy of `nrf_common.ld` (INSERT-with-`-T` was unreliable in this binutils).
- **`--whole-archive`** on the vendor libs so their `.dw_drivers` driver-registration
  structs are pulled in (else the radio never registers a driver).

### Hardware test (2026-07-07): builds + flashes, boots partway, doesn't reach USB
Flashed `cli-firmware.hex` via J-Link (mass_erase + program + verify → **Verified
OK**). On boot:
- Correct reset vector (SP = 0x20020000) and, after a clean flash, `.data` copies
  and `.bss` zeroes correctly (verified `init_array[0]` in RAM = `frame_dummy`).
  (An earlier "erased .data" reading was a glitch from probing flash while the CPU
  was wedged in a fault — the flash image is fine.)
- BUT the native-USB console (J20, Nordic VID 0x1915) **never enumerates** on any
  host, and the CPU sits in an ambiguous halt state → the image **hangs/faults
  before USB/app init**. So there are more toolchain-porting bugs beyond data-init
  (candidates: FPU/CPACR enable, FreeRTOS heap/stack sizing, `_start`/crt0 vs the
  Nordic startup's `__STARTUP_CLEAR_BSS`, or a prebuilt-lib assumption).
- **Board restored to the vendor `cli.hex`** (boots fine, J20 console responds,
  version 0.1.1-221028). The from-scratch image is a proven-buildable
  proof-of-concept, not yet a working boot.

**Takeaway:** the Pi *build* path is real (the big unknown — solved). But getting a
fully-booting image is more embedded debugging; **SES-on-Mac** (which builds the
project exactly as shipped) is the lower-risk path to a *working* image if the
boot bugs prove stubborn.

### ★ SOLVED (2026-07-07 session 3): boot hang root-caused and FIXED — Pi build fully works ★

The GCC-built CLI firmware now **builds, flashes, boots, and runs** on the Pi:
J20 console live (`VERSION:0.1.1-260707`), LISTENER2 + LSTAT polling works
(`tools/detect.py` runs against it), `SAVE` works, and saved config persists
across reboot. No SES, no Mac, no SoftDevice yet.

**Root cause of the hang:** not a toolchain/CRT bug at all. The generated
linker script placed the `.fconfig` section **inline in `.text`**, right
before the `.dw_drivers` table. But the app treats the flash page containing
`__fconfig_start` as its rewritable NVM config area — `config.c` does
`nrf_nvmc_page_erase((uint32_t)&__fconfig_start)` on config save. So on the
first save (AppConfigInit rewrites config when the CRC is missing after a
mass-erase), the firmware **erased 4KB of its own flash**, wiping the
`.dw_drivers` table. `uwb_init()` then read a garbage ops pointer from the
clobbered table (the word it fetched, `0x0A2E0000`, was config data) and
**BusFaulted** (CFSR=0x8200 PRECISERR, BFAR=0x0A2E0024). The default
`b .` handler left the CPU in the "unknown state" we kept seeing.

**Fix (in `firmware/gen_makefile.py`, tests in `tests/test_gen_makefile.py`):**
emit the vendor SES memory map instead of one flat FLASH region:
- `VECTORS` at `0x0` (0x1000) — `.isr_vector` only
- `FCONFIG` at `0x1E000` (0x1000) — `.fconfig` **alone on its own erase page**
  (matches SES `FCONFIG_START=0x1E000`)
- `FLASH` (code) from `0x1F000` (matches SES `INIT_START=0x1F000`)

Bonus: the resulting 0x1000–0x1CFFF hole is exactly where a SoftDevice goes
(Qorvo clearly designed the map for the BLE-enabled QANI build) — so the
SoftDevice step won't need another layout upheaval.

**How it was found — breadcrumb debugging (works in this sandbox):**
`firmware/debug/breadcrumb.c` + `-Wl,--wrap=` on each init call in `main()`
writes stage markers to a reserved RAM window at `0x2001FFE0` (shrink RAM to
`0x1FFE0` in `merged.ld`), and strong `HardFault_Handler` /
`app_error_fault_handler` overrides capture PC/LR/CFSR/HFSR/BFAR and spin
instead of sleeping/resetting. Read back after boot with one-shot OpenOCD
`dump_image`. First run pinpointed "entered `uwb_init`, BusFault, BFAR
garbage" in one shot; a full-RAM `dump_image` + offline pointer walk plus an
on-chip flash dump of the driver table (mismatch vs the ELF) nailed the
self-erase. Keep this harness for future firmware bring-up.

### Debug session 2 (gdb attempt) — narrowed, then blocked by tooling
- The hang is **after** C-runtime init but **before** USB/app init. Halting the
  running firmware always reports **"target in unknown state"** and then any
  memory access stalls → the CPU is in **deep sleep or a lockup** by then. That
  points at an **early error path**: an `app_error`/assert, or a clock/peripheral
  init failure (e.g. LFCLK start) that routes into a handler which sleeps.
- **Environment can't sustain interactive debugging.** A persistent OpenOCD gdb
  server is killed (exit 144 — sandbox reaps long-running USB processes), so
  `gdb-multiarch` can't stay attached to set breakpoints / single-step boot.
  One-shot OpenOCD works (flashing), but can't step through boot.
- Next real progress needs a **proper debugger**: the Mac (SES + J-Link) or a
  non-sandboxed host running `openocd` + `gdb`. Then break at `main`, then at the
  clock/`app_error`/board-init calls to find the exact stall.

### Tooling notes (Pi)
- OpenOCD `mdw`/`reg` output doesn't come through in `-c` batch here — use
  `dump_image` to read memory; to read a core reg, `mww` it into RAM then
  `dump_image` that word.
- If OpenOCD suddenly gets **no output / exit 1** on every J-Link access, the
  **J-Link OB has wedged** (common after killed sessions). Recover with a USB
  reset: `USBDEVFS_RESET` ioctl (0x5514) on `/dev/bus/usb/BBB/DDD`, or replug J9.
- To flash after the app enters sleep/lockup, use `reset halt` (not plain `halt`).

## Firmware architecture (either build path)

1. Baseline: build the **stock CLI** unchanged → confirm a known-good image and
   flash/boot loop (flash locally via the Pi's J-Link + `openocd` — this step
   does NOT depend on WiFi/SSH).
2. Add a **SoftDevice** (S113 — peripheral role, nRF52833) + nRF BLE stack
   (`nrf_sdh`, `ble_gatts`). Shift the app past the SoftDevice in flash.
3. Add a **custom GATT service** mirroring `SERVICE_UUID`/`CHAR_UUID` from
   `uwb_explorer/ble.py`, notifying the same compact JSON (`blecodec.py`).
4. Feed it from the on-chip **UWB listener + PHY counters** (the CLI already
   reads these — reuse that path to compute the level/hits).
5. FreeRTOS + SoftDevice coexistence is supported by Nordic; keep or drop the
   USB console as needed.

## Flashing (unchanged, reliable)
`tools/flash.sh` + `openocd` over the J-Link (J9) — local, no network. Keep the
CLI image around; swap back anytime for the Pi/Python path.

## ★ DONE (2026-07-08): board-only BLE firmware WORKS end-to-end ★

`firmware/build-ble.sh` → `tools/flash.sh ble` produces the goal firmware:
**S113 SoftDevice + UWB LISTENER2 + BLE GATT + USB CLI console, all at once,
built on the Pi.** The board advertises as **"UWB"** (service
`6e5f0001-b5a3-f393-e0a9-e50e24dcca9e`), auto-starts the listener at boot,
and notifies the `blecodec.py` compact JSON every 500 ms from the live LSTAT
counters (`{"s":"live",...,"c":9,"k":9}`). Verified from the Pi with `bleak`
(connect/read/subscribe, 500 ms cadence, all 8 contract keys, byte-format
identical to Python); survives reboot; `SAVE` persists. The iOS app
(`ios/`) and nRF Connect see it without changes. Board is currently running
this image; vendor restore stays `tools/flash.sh cli` / `ni`.

### Architecture
- `firmware/ble/ble_app.c` — SD enable (pre-scheduler, hooked via
  breadcrumb.c's `__wrap_osKernelStart` under `BLE_BUILD`), vs-UUID GATT
  service + raw advertising, 500 ms notify task.
- `firmware/ble/detector.c` — pure-C port of `webmodel.py` DetectorState +
  `blecodec.py` encoder; host-compiled and **byte-for-byte oracle-tested
  against the Python implementation** (`tests/test_c_detector.py`).
- `firmware/ble/uwb_feed.c` — listener autostart (same path as the
  `LISTENER2` command; respects a user-saved non-STOP default app), 1 Hz
  counter folds (SFDD/PHE/CRCB/CRCG under `taskENTER_CRITICAL`, mirroring
  the vendor's LSTAT), chan/pcode from `get_dwt_config()`.
- `firmware/ble/sd_flash_wrap.c` — `--wrap=save_bssConfig`: SD-safe
  DEFERRED config save (see gotchas).
- `firmware/ble/app_config.h` — sdk_config overlay via `USE_APP_CONFIG`.
- Memory map: app vectors @ **0x1C000** (S113 end), fconfig page @ 0x1E000
  unchanged, code @ 0x1F000, app RAM @ 0x20002608 (SD actually needs
  0x20002210 — ~1 KB reclaimable), MSP stack/newlib heap 4 KB each.

### SoftDevice-coexistence gotchas (each cost a debug cycle)
1. **NVIC sanitize before `sd_softdevice_enable`** — the vendor app inits
   every peripheral before the SD exists, so enable returns 0x1001
   (`NRF_ERROR_SDM_INCORRECT_INTERRUPT_CONFIGURATION`). Fix:
   `nvic_sanitize_for_sd()` disables SD-owned peripheral IRQs and remaps
   priorities 0/1/4/5 → 2/6 (ble_app.c).
2. **The legacy-config trap.** sdk_config defines `RTC_ENABLED`/
   `TIMER_ENABLED`, and `integration/nrfx/legacy/apply_old_config.h`
   **overrides every `NRFX_*_ENABLED` with the legacy value** — overriding
   only the NRFX keys in app_config.h silently does nothing. Override the
   LEGACY instance keys (`RTC0/RTC2/TIMER0/TIMER1_ENABLED`). The vendor HAL
   is pre-wired: RTC_ID→2, TIMERC_ID→1 ("SD using 0" comments) once the
   right instances are enabled. Miss this and TIMERC lands on the SD's
   TIMER0 → SD assert at listener start.
3. **SPIM3 anomaly-198 workaround must be OFF.** The vendor config enables
   the nRF52840 workaround, which writes the undocumented POWER register
   `0x40000E00` around **every DW3110 SPI transfer** →
   `NRF_FAULT_ID_APP_MEMACC` (fault id 0x1001, MWU peripheral violation) on
   the first SPI after SD enable. This chip is an nRF52833 — the anomaly
   doesn't apply. (Boot-time SPI works because the SD isn't enabled yet;
   the crash waits for the listener start. Very confusing.)
4. **Config save under the SD.** `f_save` wraps the save in
   `CMD_ENTER_CRITICAL()`; direct NVMC corrupts SD radio timing and calling
   `sd_flash_*`/`vTaskDelay` from that context asserts the SD (svc 255).
   Fix: `--wrap=save_bssConfig` snapshots rconfig+CRC and returns; the BLE
   notify task performs erase+write via `sd_flash_*` with SoC-event
   completion (~1 s after the "ok").
5. `NRF_SDH_DISPATCH_MODEL 2` (polling) — `nrf_sdh_freertos.c` owns
   `SD_EVT_IRQHandler`; model 0 duplicates the symbol.
6. SoftDevice headers must PRECEDE `drivers_nrf/nrf_soc_nosd` on the
   include path (`gen_makefile --inc` prepends): the no-SD `nrf_error.h`
   compiles to nothing under `SOFTDEVICE_PRESENT`.
7. Fault triage decoder ring: fault id 0x0001 = SD assert, **0x1001 =
   app invalid memory access** (MWU; NOT "SDM error 0x1001"), 0x4001 =
   SDK error via `APP_ERROR_CHECK` (err code in `info->err_code`).
   MWU REGION[0] on this setup = the SD's RAM (`0x20000000..0x2000220F`).

### Remaining polish (optional)
- AirTag live-hit test over BLE (hits>0 end-to-end) — logic is identical to
  `tools/detect.py`'s proven LSTAT path, but not yet observed with real
  UWB traffic on the BLE build.
- Reclaim ~1 KB RAM (`--ram-base 0x20002210`); drop breadcrumbs for a
  "release" image; iOS field test.

## Reproduce the build
```
# after extracting the SDK to .fwbuild/ (see fw-downloads/, gitignored)
python3 firmware/gen_makefile.py <emProject> .fwbuild/build-cli
make -C .fwbuild/build-cli
```
`firmware/gen_makefile.py` is the only build artifact tracked in git; the SDK and
`.fwbuild/` build tree are gitignored.
