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
| `XZ1` | fuzzer | start | `{}` (fires the default case, `bad-crc`) |
| `XZ1 case=oversized-phr` | fuzzer | start | `{"case": "oversized-phr"}` |
| `XZ0` | fuzzer | stop | `{}` |
| `XZ?` | fuzzer | status | `{}` |

## Fuzzer (`Z`) — AUTHORIZED SECURITY-RESEARCH TOOLING, own devices only

**Fire fuzz cases ONLY at UWB hardware you own or are explicitly authorized to
test. Never point the fuzzer at infrastructure or third-party devices.** The
fuzzer (`uwb_explorer/experiments/fuzzer.py::FuzzerController`, bead
uwb-qorvo-1hu.16) transmits one deliberately malformed 802.15.4z frame per
`start()` call — there is no auto-fire path anywhere in the controller or the
web panel that drives it; every fire is one manual button press mapping to one
`XZ1` opcode.

`XZ1`'s optional `case` arg picks a case from the fixed catalog (ordered by
id — the .15 firmware side, deferred to hardware, uses the same ids):

| id | name |
|---|---|
| 0 | `bad-crc` (default) |
| 1 | `invalid-frametype` |
| 2 | `oversized-phr` |
| 3 | `truncated-mac` |
| 4 | `illegal-sts` |

`start()` emits `fuzztx <id>` over the CLI serial link, switches the board to
LISTENER mode, and drains whatever shows up right after into a structured,
timestamped `reactions` log. `status()` reports
`{"running": bool, "last_case": str | None, "reactions": [...]}`. `stop()`
sends `stop` and restores IDLE, same as every other experiment controller.

The web dashboard's Fuzzer panel (`uwb_explorer/web.py`) carries a PROMINENT,
always-visible "Authorized targets / own devices only" note alongside the case
picker, the single **Fire** button, **Stop**, and the reactions log.

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

## Round-trip guarantee

`format_command` renders a command back to its canonical string: with args it is
`X<exp><action> k=v,k2=v2` (dict order preserved); with no args there is no
trailing space (e.g. `XT0`). `parse_command(format_command(cmd)) == cmd` holds
for any parsed command, so the Pi can echo a normalized opcode back to iOS
without drift.
