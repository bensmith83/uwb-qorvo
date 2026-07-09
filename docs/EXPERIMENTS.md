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

## Round-trip guarantee

`format_command` renders a command back to its canonical string: with args it is
`X<exp><action> k=v,k2=v2` (dict order preserved); with no args there is no
trailing space (e.g. `XT0`). `parse_command(format_command(cmd)) == cmd` holds
for any parsed command, so the Pi can echo a normalized opcode back to iOS
without drift.
