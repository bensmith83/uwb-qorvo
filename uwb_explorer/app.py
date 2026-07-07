"""UWB Explorer — Textual dashboard for the DWM3001CDK CLI firmware.

Run: python -m uwb_explorer [PORT]
Keys: l listener · i initiator · r responder · c channel 5/9 · s stop · q quit
"""

from __future__ import annotations

import time
from collections import Counter, deque

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, RichLog, Static

from .device import Device
from .mac import decode_frame
from .parser import Ack, InfoBlock, ListenerFrame, RangingResult


def rssi_bar(dbm: float | None, width: int = 10) -> str:
    """-95 dBm -> empty, -45 dBm -> full."""
    if dbm is None:
        return " " * width
    frac = min(1.0, max(0.0, (dbm + 95) / 50))
    n = round(frac * width)
    return "█" * n + "░" * (width - n)


class StatsPanel(Static):
    pass


class RangePanel(Static):
    pass


class ExplorerApp(App):
    TITLE = "UWB Explorer — DWM3001CDK"

    CSS = """
    #feed { width: 3fr; border: solid $accent; }
    #side { width: 1fr; min-width: 32; }
    StatsPanel, RangePanel { border: solid $accent; padding: 0 1; height: 1fr; }
    """

    BINDINGS = [
        ("l", "listener", "Listen (sniff)"),
        ("i", "initiator", "Range: initiator"),
        ("r", "responder", "Range: responder"),
        ("c", "channel", "Channel 5/9"),
        ("s", "stop", "Stop"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, device: Device, channel: int | None = None):
        super().__init__()
        self.device = device
        self.channel = channel
        self.frame_count = 0
        self.frame_types: Counter[str] = Counter()
        self.addrs: dict[str, float] = {}
        self.last_rssi: float | None = None
        self.distances: deque[int] = deque(maxlen=200)
        self.last_range: RangingResult | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            yield RichLog(id="feed", highlight=False, markup=False, max_lines=2000)
            with Vertical(id="side"):
                yield StatsPanel(id="stats")
                yield RangePanel(id="range")
        yield Footer()

    def on_mount(self) -> None:
        self.feed = self.query_one("#feed", RichLog)
        self.feed.write(f"connected · fw {self.device.version or '?'} · "
                        f"apps {','.join(self.device.apps) or '?'}")
        cfg = self.device.get_uwbcfg()
        if cfg:
            self.channel = cfg.get("CHAN", self.channel)
        self.refresh_side()
        self.set_interval(0.1, self.drain)
        self.sub_title = f"ch {self.channel} · mode {self.device.mode}"

    # ---- actions ----

    def action_listener(self) -> None:
        self.device.start_listener()
        self.note("LISTENER started — sniffing UWB frames")

    def action_initiator(self) -> None:
        self.device.start_ranging("initf")
        self.note("INITF started — ranging as initiator (needs a responder)")

    def action_responder(self) -> None:
        self.device.start_ranging("respf")
        self.note("RESPF started — ranging as responder (visible to initiators)")

    def action_channel(self) -> None:
        new = 5 if self.channel == 9 else 9
        self.device.stop()
        if self.device.set_channel(new):
            self.channel = new
            self.note(f"switched to UWB channel {new}