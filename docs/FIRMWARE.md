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

Not yet validated on hardware: flash `cli-firmware.hex` via the J-Link and confirm
the CLI console responds (factory backup + `firmware/cli.hex` are the safety net).

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

## Next steps
1. **Flash + verify the baseline** (`cli-firmware.hex`) via the Pi's J-Link and
   confirm the CLI console still works — needs the board's J9 on a flashing host.
2. Add a **SoftDevice (S113)** + nRF BLE stack; shift the app in flash (new
   linker origin) and merge the SoftDevice hex when flashing.
3. Add the **BLE GATT service** mirroring `uwb_explorer/ble.py`'s UUIDs +
   `blecodec.py` wire format, fed from the on-chip UWB listener counters.

## Reproduce the build
```
# after extracting the SDK to .fwbuild/ (see fw-downloads/, gitignored)
python3 firmware/gen_makefile.py <emProject> .fwbuild/build-cli
make -C .fwbuild/build-cli
```
`firmware/gen_makefile.py` is the only build artifact tracked in git; the SDK and
`.fwbuild/` build tree are gitignored.
