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
