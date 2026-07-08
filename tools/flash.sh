#!/usr/bin/env bash
# Flash a firmware personality onto the DWM3001CDK via its onboard J-Link.
# Uses OpenOCD (pyocd cannot drive the J-Link OB without SEGGER's library).
# Usage: tools/flash.sh <hexfile | cli | ni | backup | restore>
set -euo pipefail
cd "$(dirname "$0")/.."

BACKUP=firmware/factory-backup.bin

ocd() {
  openocd -f interface/jlink.cfg -c "transport select swd" \
    -f target/nordic/nrf52.cfg -c "init; halt; $1; exit"
}

case "${1:-}" in
  backup)
    if [[ -s $BACKUP ]]; then echo "Backup already exists: $BACKUP"; exit 0; fi
    ocd "flash read_bank 0 $BACKUP 0 0x80000; flash read_bank 1 firmware/factory-uicr.bin; reset run"
    ls -la "$BACKUP"
    ;;
  restore|ni)
    # Factory firmware IS the Nearby Interaction (QANI) personality.
    [[ -s $BACKUP ]] || { echo "No backup at $BACKUP"; exit 1; }
    ocd "nrf5 mass_erase; program $BACKUP 0x0; reset run"
    echo "Restored factory QANI (Nearby Interaction) firmware."
    ;;
  cli)
    exec "$0" firmware/cli.hex
    ;;
  ble)
    # our BLE build: S113 SoftDevice at 0x0 + app at 0x1C000
    SD=.fwbuild/DWM3001CDK-DW3_QM33_SDK-FreeRTOS/SDK_BSP/Nordic/NORDIC_SDK_17_1_0/components/softdevice/s113/hex/s113_nrf52_7.2.0_softdevice.hex
    APP=.fwbuild/build-ble/ble-firmware.hex
    [[ -s $SD && -s $APP ]] || { echo "Missing $SD or $APP (run firmware/build-ble.sh)"; exit 1; }
    [[ -s $BACKUP ]] || { echo "Refusing to flash before factory backup exists."; exit 1; }
    ocd "nrf5 mass_erase; program $SD; program $APP; reset halt; mww 0x2001FF80 0 32; resume"
    echo "Flashed S113 + ble-firmware and reset."
    ;;
  *.hex)
    [[ -s $1 ]] || { echo "Missing file: $1"; exit 1; }
    [[ -s $BACKUP ]] || { echo "Refusing to flash before factory backup exists. Run: tools/flash.sh backup"; exit 1; }
    ocd "nrf5 mass_erase; program $1; reset run"
    echo "Flashed $1 and reset."
    ;;
  *)
    echo "Usage: $0 <hexfile | cli | ni | backup | restore>"; exit 1
    ;;
esac
