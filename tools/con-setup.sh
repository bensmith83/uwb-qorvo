#!/usr/bin/env bash
# One-time setup to make this Pi a self-contained, battery-powered UWB
# explorer for a con: (1) auto-launch the phone dashboard on boot, and
# (2) broadcast its own WiFi hotspot so your phone connects with no network.
#
#   RUN THIS ON THE PORTABLE PI (e.g. the Pi Zero W) — NOT on your dev host.
#   It changes networking (starts an access point) and installs a service.
#
#   sudo ./tools/con-setup.sh            # set it all up
#   sudo ./tools/con-setup.sh --undo     # tear it back down
#
# Tunables (override via env):
#   SSID, PASS, PORT
set -euo pipefail

SSID="${SSID:-UWB-Explorer}"
PASS="${PASS:-uwbexplorer}"     # >= 8 chars (WPA2 minimum)
PORT="${PORT:-80}"
SERVICE=uwb-dashboard
HOTSPOT=uwb-hotspot
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="$REPO/venv/bin/python"
RUN_USER="${SUDO_USER:-pi}"

if [[ "${1:-}" == "--undo" ]]; then
  systemctl disable --now "$SERVICE" 2>/dev/null || true
  rm -f "/etc/systemd/system/$SERVICE.service"
  systemctl daemon-reload
  if command -v nmcli >/dev/null; then
    nmcli con down "$HOTSPOT" 2>/dev/null || true
    nmcli con delete "$HOTSPOT" 2>/dev/null || true
  fi
  echo "Removed the dashboard service and hotspot."
  exit 0
fi

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo (installs a service + configures WiFi)." >&2
  exit 1
fi
if [[ ! -x "$PY" ]]; then
  echo "No venv python at $PY — create the venv first." >&2
  exit 1
fi

echo ">> Installing autostart service ($SERVICE) on port $PORT ..."
cat > "/etc/systemd/system/$SERVICE.service" <<EOF
[Unit]
Description=UWB Explorer phone dashboard
After=network.target

[Service]
Type=simple
WorkingDirectory=$REPO
# Port 80 needs privilege; the board is a throwaway con appliance so root is fine.
ExecStart=$PY -m uwb_explorer.web --host 0.0.0.0 --port $PORT --sweep
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now "$SERVICE"

echo ">> Configuring WiFi hotspot \"$SSID\" ..."
if command -v nmcli >/dev/null; then
  # NetworkManager path (Raspberry Pi OS Bookworm and newer). Gateway is 10.42.0.1.
  nmcli con delete "$HOTSPOT" 2>/dev/null || true
  nmcli con add type wifi ifname wlan0 con-name "$HOTSPOT" autoconnect yes ssid "$SSID"
  nmcli con modify "$HOTSPOT" \
    802-11-wireless.mode ap 802-11-wireless.band bg \
    ipv4.method shared \
    wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$PASS"
  nmcli con up "$HOTSPOT"
  GW="10.42.0.1"
else
  echo "!! nmcli (NetworkManager) not found."
  echo "   This Pi likely uses the older dhcpcd/hostapd stack. The dashboard"
  echo "   service is installed and running; set up the AP with hostapd +"
  echo "   dnsmasq manually, or (easiest) re-image with Raspberry Pi OS Bookworm"
  echo "   which ships NetworkManager, then re-run this script."
  GW="<pi-ip>"
fi

if [[ "$PORT" == "80" ]]; then URL="http://$GW"; else URL="http://$GW:$PORT"; fi

cat <<EOF

============================================================
 UWB Explorer con kit is armed.
   1. Power the Pi from any USB battery.
   2. On your iPhone, join WiFi:  $SSID   (password: $PASS)
   3. Open:  $URL
 The dashboard auto-starts on every boot. Plug the DWM3001CDK
 into the Pi's USB (J20) any time — it shows "waiting" until then.
 Undo everything:  sudo ./tools/con-setup.sh --undo
============================================================
EOF
