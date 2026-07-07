# UWB Explorer — the terminal dashboard

Turns the DWM3001CDK into a live UWB scanner driven from the Pi. Built on
the Qorvo QM33 **CLI firmware** (`firmware/cli.hex`).

## One-time wiring

The CLI console is exposed on the board's **J20** micro-USB port (the
nRF52833's *native* USB), **not** the J-Link port (J9). So:

- **J9** (J-Link) → Pi: used to *flash* firmware (and power).
- **J20** (native USB) → Pi: the *CLI console* the dashboard talks to.

Plug BOTH into the Pi. J20 enumerates as a second `/dev/ttyACM*`
(Nordic VID 0x1915); the dashboard auto-selects it.

## Run

```bash
./run.sh flash-cli      # once, to put the CLI firmware on the board
./run.sh dash           # launch the live dashboard
# or a raw REPL:
./run.sh console
```

Keys in the dashboard: `l` listen (sniff), `i` initiator, `r` responder,
`s` stop, `5`/`9` switch channel, `c` clear, `q` quit.

## What each mode explores

- **LISTENER** — promiscuous PHY sniffer. Prints every UWB frame it hears
  on the current channel/config (`UWBCFG`), with signal level (`rsl`) and
  first-path level (`fsl`). The dashboard decodes the 802.15.4z MAC header
  where possible (frame type, addresses) and lists each source address as a
  passive "contact". This is the "walk around and see what UWB is out
  there" mode.
- **INITF / RESPF** — FiRa two-way ranging. Two DWM boards (or this board +
  another FiRa device) measure distance and angle-of-arrival. Output is
  distance in cm plus PDoA/AoA. Both ends must share channel + session id +
  vUpper64.

## Honest limits (worth knowing)

- The listener only decodes frames matching its configured PHY (channel,
  preamble code, SFD, STS mode). Commercial UWB (car keys, AirTag precision
  finding, other phones) may use a different channel or encrypted STS, so
  you'll often see it as *frame energy / partial headers* rather than fully
  decoded packets. Sweeping channel 5 vs 9 (`5`/`9` keys) helps.
- FiRa ranging needs a second UWB device. With just this board, your
  **iPhone is the second device** — but note the iPhone talks Apple's
  Nearby Interaction profile, which pairs with the QANI firmware
  (`./run.sh flash-ni`), not the CLI ranging mode. So: use CLI+listener to
  *sniff*, or QANI to *range with the phone* — one personality at a time.

## Data model

`uwb_explorer/` is a small, tested Python package:
- `transport.py` — line framing over serial (fake-serial tested)
- `parser.py` — CLI output → typed events (handles both SDK output formats)
- `mac.py` — best-effort 802.15.4z MAC header decode
- `device.py` — firmware detect + app control (STAT/LISTENER/INITF/UWBCFG)
- `radar.py` — rolling contact/stat aggregation
- `tui.py` — the Textual dashboard
Run `./run.sh test` — 42 tests.
