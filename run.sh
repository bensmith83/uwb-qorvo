#!/usr/bin/env bash
# Convenience launcher for the UWB Explorer.
#   ./run.sh dash      -> live TUI dashboard (default)
#   ./run.sh web       -> phone web dashboard (the portable/con build)
#   ./run.sh console   -> raw interactive CLI console
#   ./run.sh test      -> run the pytest suite
#   ./run.sh flash-cli -> flash the sniffer/ranging firmware
#   ./run.sh flash-ni  -> restore the iPhone Nearby-Interaction firmware
set -euo pipefail
cd "$(dirname "$0")"
PY=./venv/bin/python
case "${1:-dash}" in
  dash)      exec $PY -m uwb_explorer.tui "${@:2}" ;;
  web)       exec $PY -m uwb_explorer.web "${@:2}" ;;
  console)   exec $PY -m uwb_explorer.console "${@:2}" ;;
  test)      exec $PY -m pytest tests/ -q ;;
  ports)     exec $PY -m serial.tools.list_ports -v ;;
  flash-cli) exec ./tools/flash.sh cli ;;
  flash-ni)  exec ./tools/flash.sh ni ;;
  *) echo "usage: $0 {dash|web|console|test|ports|flash-cli|flash-ni}"; exit 1 ;;
esac
