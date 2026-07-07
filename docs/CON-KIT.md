# Con kit — the portable UWB explorer

Goal: a pocket device you power from a battery and carry around a con floor.
Your phone shows a live UWB "Geiger counter" — it lights up near cars
unlocking, phones doing precision-finding, and AirTags.

## Why it's a 3-part bundle (not just the board)

Ultra-wideband lives **only** on the DWM3001CDK (the Qorvo DW3110 radio). A
Thingy:52/:53 has **no UWB** and can't do this. But the board can't run the
detector by itself — the detection logic runs in Python on a small Linux host.
So the unit is: **board + tiny Pi + battery**, with your phone as the screen.

```
 USB battery ──┬─► Pi Zero W  ──(USB/OTG)──► DWM3001CDK (CLI firmware)
               │      │
               │      └─ runs the dashboard + its own WiFi hotspot
   iPhone ─────┘  (join "UWB-Explorer" WiFi, open the page)
```

## Bill of materials

| Part | Notes |
|------|-------|
| DWM3001CDK | you have it. Flash the **CLI** firmware: `./run.sh flash-cli` |
| Raspberry Pi Zero W | you have one. A Pi Zero 2 W is a faster drop-in, not required |
| USB power bank | any 5000 mAh+; the Pi + board draw ~1–2 W, so hours of runtime |
| micro-USB → USB-A OTG adapter | for the Pi Zero's **USB** (data) port |
| USB-A → micro-USB cable | from that adapter to the board's **J20** port |
| micro-USB power cable | battery → the Pi Zero's **PWR IN** port |

## Cabling

- Pi Zero has two micro-USB ports: **PWR IN** (power only) and **USB** (data/OTG).
  Power the Pi from the battery into **PWR IN**.
- The Pi is the USB *host*; the board is the *device*. Connect the Pi's **USB**
  (OTG) port → OTG adapter → cable → the board's **J20** (the Nordic native-USB
  port — the CLI console is here, not on the J-Link port).

### Verify once: does the board run on J20 power alone?
Powering the board over J20 *should* also power it. On the bench, plug only J20
to the Pi and run `./run.sh web`: if the dashboard goes **live** (shows a
channel), you're done. If the board doesn't come up, also feed 5 V to the
board's **J9** port from the battery (a 2-port battery or a small USB hub), then
J20 is data-only. This is the one thing worth checking before the con.

## First-time setup (run on the Pi Zero, once)

```bash
./run.sh flash-cli          # put the sniffer firmware on the board (needs J-Link/J9)
./run.sh test               # sanity: 60 tests green
sudo ./tools/con-setup.sh   # autostart on boot + broadcast the WiFi hotspot
```

`con-setup.sh` installs a systemd service (`uwb-dashboard`) that launches the
dashboard on every boot with preamble-code sweep enabled, and creates a WiFi
access point via NetworkManager. Defaults (override with env vars):

- SSID **UWB-Explorer**, password **uwbexplorer**, port **80**
- e.g. `sudo SSID=myuwb PASS=hunter2000 ./tools/con-setup.sh`

Tear it all down with `sudo ./tools/con-setup.sh --undo`.

## At the con

1. Press the battery on. Wait ~30 s for the Pi to boot.
2. On your iPhone, join WiFi **UWB-Explorer** (password **uwbexplorer**).
3. Open **http://10.42.0.1** (the NetworkManager hotspot gateway).
4. Walk around. The meter climbs and turns amber/red as UWB frames hit the
   antenna; the sparkline shows the last minute. It reads "waiting for board"
   until the DWM is plugged into J20, so you can hot-plug it any time.

## Good things to point it at

- A car with digital-key / UWB unlock as someone approaches it.
- An iPhone running **Find My → precision finding** to an AirTag (hold both near
  the board — this is the capture we already proved, see `docs/FINDINGS.md`).
- Newer phones ranging to each other (AirDrop proximity, some access badges).

Encrypted UWB (Apple STS) shows as **energy** (SFDD/PHE counters climb) rather
than decoded bytes — that's still a real, honest detection. Fully decoded
frames (good CRC) light the "decoded ✓" note when they occur.

## Alt: the no-Pi pocket demo

If you just want a zero-setup icebreaker: `./run.sh flash-ni` restores the
factory Nearby-Interaction firmware, power the board off the battery alone, and
open the **Qorvo Nearby Interaction** app on your iPhone — it shows live
distance/direction to the board. No Pi, no dashboard. (One firmware at a time;
`flash-cli` / `flash-ni` swap in seconds.)
