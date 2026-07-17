# Experiment control opcodes — the `X<exp><action>` grammar

This is the shared contract between three sides that must all agree on it:

- the **board firmware**'s 6e5f control characteristic, which accepts short
  string commands over BLE,
- the **Pi dispatcher** (`uwb_explorer/experiments/control.py`), which parses an
  opcode and routes it to the right experiment controller, and
- the **iOS app**, which builds these strings and writes them to the
  characteristic.

The grammar is pinned in code in `uwb_explorer/experiments/control.py`
(`EXPERIMENTS`, `ACTIONS`, `parse_command`, `format_command`, `Dispatcher`) the
same way `blecodec.KEY_MAP` pins the BLE snapshot wire format. If you change the
table here, change it there too.

## Existing (non-experiment) control commands

For context, the control characteristic already understands two older command
families, which are **not** part of this grammar:

| Command | Meaning |
|---|---|
| `F1` / `F0` | Toggle CRC-fail frame capture on / off. |
| `S<n>` | Pick an STS receive mode (`S0`/`S1`/`S2`/`S3`). |

The experiment opcodes below are distinguished from those by their leading `X`.

## The opcode

    X<exp><action>[ <args>]

- **`X`** — literal prefix marking an experiment opcode.
- **`<exp>`** — one letter picking the experiment.
- **`<action>`** — one char picking the verb.
- **`<args>`** — *optional*. When present it is separated from the opcode by a
  **single space**, and is a CSV of `key=value` pairs. Values are opaque
  strings (no numeric coercion); key order is preserved.

> **List-valued args use `;` as the sub-delimiter.** Because `,` already
> separates the `key=value` pairs, a value that is itself a *list* joins its
> elements with a semicolon `;` on the wire, not a comma. For example the
> scanner start command with channels 5 and 9 and preamble codes 9–12 is
> `XS1 channels=5;9,pcodes=9;10;11;12` — the `,` splits it into the two pairs
> `channels=5;9` and `pcodes=9;10;11;12`, and each value is a `;`-joined list.
> The scanner controller accepts both `;` and `,` inside a single value.

### Experiments (`<exp>`)

| Letter | Experiment |
|---|---|
| `S` | scanner |
| `T` | transponder |
| `B` | beacon |
| `Z` | fuzzer |

### Actions (`<action>`)

| Char | Verb |
|---|---|
| `1` | start |
| `0` | stop |
| `?` | status |

The verb name doubles as the controller method the dispatcher calls
(`start(args)` / `stop(args)` / `status(args)`).

## Worked examples

| Opcode | exp | action | args |
|---|---|---|---|
| `XS1` | scanner | start | `{}` |
| `XS1 chan=9,pcode=10` | scanner | start | `{"chan": "9", "pcode": "10"}` |
| `XS0` | scanner | stop | `{}` |
| `XS?` | scanner | status | `{}` |
| `XT1` | transponder | start | `{}` |
| `XT0` | transponder | stop | `{}` |
| `XB1 payload=deadbeef` | beacon | start | `{"payload": "deadbeef"}` |
| `XB?` | beacon | status | `{}` |
| `XZ1` | fuzzer | start | `{}` |

## Malformed opcodes (rejected)

`parse_command` raises `ValueError` on any of these:

| Input | Why it is rejected |
|---|---|
| `""` | empty |
| `S1` | missing `X` prefix |
| `XQ1` | `Q` is not a known experiment letter |
| `XS9` | `9` is not a known action char |
| `XS` | missing action |
| `X` | nothing after the prefix |
| `XS1chan=9` | args must be separated from the opcode by a space |

## Fuzzer (`Z`) — malformed-frame emission

> **Ethics & scope.** The fuzzer is **authorized security-research tooling**.
> It deliberately transmits **malformed IEEE 802.15.4z frames** to probe how a
> UWB receiver handles non-conformant input. Use it **only on devices you own
> or are explicitly authorized to test.** Emission is **opcode-triggered
> only** — the board never radiates a malformed frame on its own. It is keyed
> either by the experiment opcode **`XZ1`** (fuzzer start) or by the board
> serial-CLI command **`fuzztx <case_id>`**; each trigger emits exactly **one**
> frame and then returns to IDLE. The fuzzer is **half-duplex**: the passive
> LISTENER2 sniffer is paused while the radio is keyed and resumed afterwards.

The `fuzztx <case_id>` CLI command selects one builder from the fuzz-case
catalog, emits a single malformed frame, and returns to IDLE. The catalog is a
**shared contract** — the firmware builders (`firmware/ble/fuzzframe.c`), the C
unit tests (`tests/test_fuzzframe.py`), and the Pi-side `FuzzerController` all
use the same ids:

| id | case | malformation |
|---|---|---|
| `0` | `bad-crc` | well-formed frame whose 2-octet FCS does not match the CRC of the body |
| `1` | `invalid-frametype` | FCF frame-type field set to `7` (Reserved); FCS otherwise valid |
| `2` | `oversized-phr` | PHR length field (`200`) larger than the real payload and the legal 127-octet maximum |
| `3` | `truncated-mac` | FCF declares short dest+src addressing, but the frame is cut off after the sequence number (addressing fields + FCS missing) |
| `4` | `illegal-sts` | inconsistent STS packet config: SP mode says STS is present (SP2) while the STS length is zero |

Example: `fuzztx 2` transmits one oversized-PHR frame. An unknown or
out-of-range case id (e.g. `fuzztx 9`) is rejected and nothing is transmitted.

> **On-board TX verification is deferred.** The builders and the
> build→pause→TX→resume dispatch are covered by host C unit tests; radiated-RF
> verification on hardware is a later healthy-board task (the boards' RF link
> is currently degraded).

## Round-trip guarantee

`format_command` renders a command back to its canonical string: with args it is
`X<exp><action> k=v,k2=v2` (dict order preserved); with no args there is no
trailing space (e.g. `XT0`). `parse_command(format_command(cmd)) == cmd` holds
for any parsed command, so the Pi can echo a normalized opcode back to iOS
without drift.
