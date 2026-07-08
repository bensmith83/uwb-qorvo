#!/usr/bin/env bash
# Build the BLE firmware variant (SoftDevice S113 + GATT service + CLI/UWB).
# Usage: firmware/build-ble.sh [--no-breadcrumbs]
set -euo pipefail
cd "$(dirname "$0")/.."

SDK=$(pwd)/.fwbuild/DWM3001CDK-DW3_QM33_SDK-FreeRTOS
N=$SDK/SDK_BSP/Nordic/NORDIC_SDK_17_1_0
EMPROJ=$SDK/Projects/DW3_QM33_SDK/FreeRTOS/DWM3001CDK/ses/DWM3001CDK-DW3_QM33_SDK_CLI-FreeRTOS.emProject
OUT=.fwbuild/build-ble

# App RAM base: S113 minimum is 0x20001198; with our BLE config
# (1 periph link, MTU 131, 1 vs-UUID) sd_ble_enable reports the real need —
# it lands in breadcrumb word [1] (0x2001FFE4). Update here if it changes.
RAM_BASE=0x20002608

python3 firmware/gen_makefile.py "$EMPROJ" "$OUT" \
  --target ble-firmware \
  --app-base 0x1c000 --ram-base $RAM_BASE \
  --define SOFTDEVICE_PRESENT --define S113 --define BLE_STACK_SUPPORT_REQD \
  --define USE_APP_CONFIG --define BLE_BUILD \
  --define __HEAP_SIZE=4096 --define __STACK_SIZE=4096 \
  --inc "$(pwd)/firmware/ble" \
  --inc "$N/components/softdevice/s113/headers" \
  --inc "$N/components/softdevice/s113/headers/nrf52" \
  --inc "$N/components/softdevice/common" \
  --inc "$N/components/ble/common" \
  --inc "$N/components/ble/nrf_ble_gatt" \
  --inc "$N/components/libraries/experimental_section_vars" \
  --src "$(pwd)/firmware/ble/ble_app.c" \
  --src "$(pwd)/firmware/ble/uwb_feed.c" \
  --src "$(pwd)/firmware/ble/detector.c" \
  --src "$(pwd)/firmware/ble/framefmt.c" \
  --src "$(pwd)/firmware/ble/sd_flash_wrap.c" \
  --wrap save_bssConfig \
  --src "$N/components/softdevice/common/nrf_sdh.c" \
  --src "$N/components/softdevice/common/nrf_sdh_ble.c" \
  --src "$N/components/softdevice/common/nrf_sdh_soc.c" \
  --src "$N/components/softdevice/common/nrf_sdh_freertos.c" \
  --src "$N/components/ble/common/ble_advdata.c" \
  --src "$N/components/ble/common/ble_srv_common.c" \
  --src "$N/components/ble/nrf_ble_gatt/nrf_ble_gatt.c" \
  --src "$N/components/libraries/experimental_section_vars/nrf_section_iter.c"

if [[ "${1:-}" != "--no-breadcrumbs" ]]; then
  # reserve the breadcrumb windows (top 64 B of RAM: boot @0x2001FFE0 +
  # BLE event log @0x2001FFC0) + wrap the boot ladder
  python3 - "$OUT" <<'EOF'
import re, sys
out = sys.argv[1]
ld = open(f"{out}/merged.ld").read()
ld = re.sub(r"(RAM \(rwx\) : ORIGIN = 0x[0-9a-f]+, LENGTH = )(0x[0-9a-f]+)",
            lambda m: m.group(1) + hex(int(m.group(2), 16) - 0x80), ld, count=1)
open(f"{out}/merged.ld", "w").write(ld)
p = f"{out}/Makefile"
s = open(p).read()
block = '''
# --- boot-hang breadcrumb instrumentation (firmware/debug/breadcrumb.c) ---
C_SRC += /home/pi/xfer/vibin/uwb-qorvo/firmware/debug/breadcrumb.c
LDFLAGS += -Wl,--wrap=SystemInit -Wl,--wrap=BoardInit -Wl,--wrap=AppConfigInit \\
  -Wl,--wrap=EventManagerInit -Wl,--wrap=board_interface_init \\
  -Wl,--wrap=uwb_init -Wl,--wrap=DefaultTaskInit -Wl,--wrap=FlushTaskInit \\
  -Wl,--wrap=ControlTaskInit -Wl,--wrap=osKernelStart \\
  -Wl,--wrap=dwt_isr -Wl,--wrap=listener_task_notify \\
  -Wl,--wrap=EventManagerWaitAppRegistration -Wl,--wrap=copy_tx_msg
'''
s = s.replace('\nall: $(TARGET).hex', block + '\nall: $(TARGET).hex', 1)
open(p, 'w').write(s)
EOF
fi

make -C "$OUT"
arm-none-eabi-size "$OUT/ble-firmware.elf"
echo "SoftDevice: $N/components/softdevice/s113/hex/s113_nrf52_7.2.0_softdevice.hex"
