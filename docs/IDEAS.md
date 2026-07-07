# Project ideas — what else this board can do

Ranked by "fun per effort," noting what extra hardware (if any) each needs.
Everything here builds on the tested `uwb_explorer/` package and `tools/`.

## Doable now — one board + Pi (+ iPhone)

### 1. UWB "geiger counter" / tricorder  ★ easiest win
Turn `tools/detect.py` into a portable, audible UWB energy meter: the faster
the PHY counters climb, the faster it clicks (buzzer/Pi audio) and the
brighter an LED. Walk around and *hear* UWB activity — cars unlocking,
phones precision-finding, AirTags. Add a battery + the Pi and it's a
handheld "UWB sniffer wand." Pure software on top of what we built.

### 2. Ambient UWB occupancy logger
Long-running `detect.py` variant that timestamps every UWB detection to a
CSV/SQLite, sweeping channels 5/9 and preamble codes. Leave it in a room,
a hallway, a parking lot; later chart *when* UWB devices appear. Answers
"how much UWB is actually around me?" Great data-viz follow-up.

### 3. AirTag / Find-My activity sniffer (extends today's win)
We proved passive detection of an iPhone-AirTag session. Package it: a live
readout that lights up whenever precision-finding happens nearby, logs the
preamble code + RSSI, and correlates bursts. A privacy/security demo piece.

### 4. Multipath explorer via DIAG mode
The CLI firmware's `DIAG 1` exposes **RSSI + NLOS (non-line-of-sight)
probability** derived from the Channel Impulse Response. Even solo you can
watch how the metric shifts as you put a wall / your body / furniture
between antenna and a reflector. "Can UWB tell it's being blocked?" — yes,
and you can visualize it. (Richest results need a 2nd device to range with.)

## Small purchase unlocks a lot — a 2nd UWB board

A second DWM3001CDK (or any FiRa board) is the single biggest unlock. Then:

### 5. Centimeter ranging → proximity toys
`INITF`/`RESPF` two-way ranging between the two boards, live distance in the
Textual dashboard (already built). Build: a "digital leash" that alarms past
N cm, a hot/cold treasure-hunt game, a contactless tape measure.

### 6. Through-wall / line-of-sight classifier
With ranging live, `DIAG 1`'s NLOS metric becomes a real "is there a wall
between us?" detector. Log LOS vs NLOS vs distance; train a tiny classifier.

### 7. UWB data link
Use TX frame modes to shuttle bytes board-to-board over UWB (not just
ranging). A quirky short-range, hard-to-intercept comms demo.

## Bigger builds

### 8. Indoor positioning (3+ boards)
Three+ boards as fixed anchors + one tag → trilaterate the tag's XY position
in a room. Live map on the Pi. This is what commercial UWB RTLS does.

### 9. Custom iOS Nearby-Interaction app  (needs a Mac + Xcode)
Flash the board back to QANI (`tools/flash.sh ni`) and write a real iOS app
against Apple's NearbyInteraction framework. Distance/direction-triggered
automations: unlock something when the phone is within 30 cm, AR overlays,
a "point me to the device" arrow of your own.

### 10. UWB radar / presence sensing
DW3000 supports impulse-radar-style sensing (monitor CIR for motion in the
reflected channel). Detect a person entering a room with no camera. Advanced;
may need custom firmware beyond the stock CLI build.

## Notes
- Half-duplex reality: one board can *either* sniff *or* range/transmit at a
  time. Any "watch two things interact" demo needs a second radio (the
  iPhone counts as one, but only speaks Apple NI, not FiRa CLI).
- `tools/flash.sh` swaps personalities (CLI <-> factory QANI) in seconds, so
  switching between the explorer and iPhone worlds is cheap.
