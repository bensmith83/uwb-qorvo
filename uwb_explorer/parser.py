"""Parse DWM3001CDK CLI firmware output lines into typed events.

See tests/test_parser.py for the verbatim formats from Qorvo's guide.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

_JS_PREFIX_RE = re.compile(r"^JS[0-9A-Fa-f]{4}")
_LSTN_RE = re.compile(r'"LSTN"\s*:\s*\[([0-9A-Fa-f,\s]*)\]')


@dataclass
class RangeEntry:
    addr: str
    status: str
    distance_cm: int | None = None
    pdoa_deg: float | None = None
    aoa_azimuth_deg: float | None = None
    fom: float | None = None
    remote_aoa_deg: float | None = None
    cfo_ppm: float | None = None


@dataclass
class RangingResult:
    block: int | None
    results: list[RangeEntry] = field(default_factory=list)


@dataclass
class ListenerFrame:
    payload: bytes
    timestamp: int
    offset: int
    rssi_dbm: float | None = None
    first_path_dbm: float | None = None


@dataclass
class InfoBlock:
    data: dict


@dataclass
class Ack:
    ok: bool


Event = RangingResult | ListenerFrame | InfoBlock | Ack


def _range_entry(r: dict) -> RangeEntry:
    # CFO field name/unit varies by SDK version: CFO_ppm vs CFO_100ppm
    cfo = r.get("CFO_ppm")
    if cfo is None and "CFO_100ppm" in r:
        cfo = r["CFO_100ppm"] / 100.0
    return RangeEntry(
        addr=r.get("Addr", "?"),
        status=r.get("Status", "?"),
        distance_cm=r.get("D_cm"),
        pdoa_deg=r.get("LPDoA_deg"),
        aoa_azimuth_deg=r.get("LAoA_deg"),
        fom=r.get("LFoM"),
        remote_aoa_deg=r.get("RAoA_deg"),
        cfo_ppm=cfo,
    )


def _parse_ranging(obj: dict) -> RangingResult:
    entries = [_range_entry(r) for r in obj.get("results", [])]
    return RangingResult(block=obj.get("Block"), results=entries)


def _parse_listener(body: str) -> ListenerFrame | None:
    m = _LSTN_RE.search(body)
    if not m:
        return None
    hexbytes = [t.strip() for t in m.group(1).split(",") if t.strip()]
    try:
        payload = bytes(int(t, 16) for t in hexbytes)
    except ValueError:
        return None
    # Quote the hex tokens so the rest parses as JSON
    quoted = _LSTN_RE.sub('"LSTN":[]', body)
    try:
        rest = json.loads(quoted)
    except json.JSONDecodeError:
        return None
    ts = rest.get("TS4ns", rest.get("TS", "0x0"))
    timestamp = int(ts, 16) if isinstance(ts, str) else int(ts)
    return ListenerFrame(
        payload=payload,
        timestamp=timestamp,
        offset=rest.get("O", 0),
        rssi_dbm=rest.get("rsl"),
        first_path_dbm=rest.get("fsl"),
    )


def parse_line(line: str) -> Event | None:
    """Parse one CLI output line; None if it carries no structured event."""
    line = line.strip()
    if not line:
        return None

    if line == "ok":
        return Ack(ok=True)
    if line.startswith("error"):
        return Ack(ok=False)

    body = _JS_PREFIX_RE.sub("", line, count=1)

    if '"LSTN"' in body:
        return _parse_listener(body)

    if body.startswith("{") or body.startswith("["):
        try:
            obj = json.loads(body)
        except json.JSONDecodeError:
            return None
        if isinstance(obj, list):
            # SDK 1.0.2 compact style: bare array of measurement dicts
            if obj and isinstance(obj[0], dict) and "Addr" in obj[0]:
                return RangingResult(block=None, results=[_range_entry(r) for r in obj])
            return None
        if "Block" in obj and "results" in obj:
            return _parse_ranging(obj)
        return InfoBlock(data=obj)

    return None
