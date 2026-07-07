"""UWB Explorer — a live terminal dashboard for the DWM3001CDK.

    python -m uwb_explorer.tui [--port /dev/ttyACM1]

Keys:
    l  start LISTENER (sniff all UWB frames in range)
    i  start INITF   (be a ranging initiator)
    r  start RESPF   (be a ranging responder)
    s  STOP the current app
    5/9 switch UWB channel
    c  clear contacts
    q  quit

Left: a "contact list" of every UWB device the board is ranging with or
overhearing. Right top: a scrolling raw event log. Right bottom: live
stats + a sparkline of the nearest contact's distance.
"""

from __future__ import annotations

import argparse
import threading

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, Header, RichLog, Sparkline, Static

from .device import Device
from .parser import Ack, InfoBlock, ListenerFrame, RangingResult
from .radar import RadarModel
from .serialport import find_cli_port, open_cli


class UwbExplorerApp(App):
    CSS = """
    #contacts { width: 46%; border: round $accent; }
    #right { width: 54%; }
    #log { height: 1fr; border: round $accent; }
    #stats { height: 9; border: round $accent; }
    Sparkline { height: 3; margin: 1 1; }
    """
    BINDINGS = [
        ("l", "listen", "Listen"),
        ("i", "initf", "Initiator"),
        ("r", "respf", "Responder"),
        ("s", "stop", "Stop"),
        ("5", "chan5", "Ch5"),
        ("9", "chan9", "Ch9"),
        ("c", "clear", "Clear"),
        ("q", "quit", "Quit"),
    ]

    mode = reactive("STOP")

    def __init__(self, port: str, **kw):
        super().__init__(**kw)
        self._port = port
        self.model = RadarModel()
        self.device: Device | None = None
        self._ser = None
        self._lock = threading.Lock()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            yield DataTable(id="contacts")
            with Vertical(id="right"):
                yield RichLog(id="log", highlight=True, markup=True, max_lines=500)
                with Vertical(id="stats"):
                    yield Static(id="statline")
                    yield Sparkline([0], id="spark")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#contacts", DataTable)
        table.add_columns("Addr", "Kind", "Dist(cm)", "N", "RSSI")
        table.cursor_type = "row"
        self._ser = open_cli(self._port)
        self.device = Device(self._ser)
        log = self.query_one("#log", RichLog)
        log.write("[bold green]Connected[/]. Detecting firmware…")
        if self.device.detect():
            log.write(f"Firmware [cyan]{self.device.version}[/] "
                      f"apps={self.device.apps}")
        self.set_interval(0.1, self._pump)
        self.set_interval(0.5, self._refresh)

    # --- serial pump (runs on the Textual event loop thread) ---
    def _pump(self) -> None:
        if not self.device:
            return
        log = self.query_one("#log", RichLog)
        for ev in self.device.poll_events():
            self.model.ingest(ev)
            self._log_event(log, ev)

    def _log_event(self, log: RichLog, ev) -> None:
        if isinstance(ev, RangingResult):
            for r in ev.results:
                d = f"{r.distance_cm}cm" if r.distance_cm is not None else "--"
                colour = "green" if r.distance_cm is not None else "red"
                log.write(f"[{colour}]RANGE[/] {r.addr} {r.status} {d} "
                          f"AoA={r.aoa_azimuth_deg}")
        elif isinstance(ev, ListenerFrame):
            extra = f" [dim]rsl={ev.rssi_dbm}[/]" if ev.rssi_dbm is not None else ""
            log.write(f"[yellow]FRAME[/] {len(ev.payload)}B "
                      f"[dim]{ev.payload[:12].hex(' ')}[/]{extra}")
        elif isinstance(ev, InfoBlock):
            log.write(f"[blue]INFO[/] {ev.data}")
        elif isinstance(ev, Ack):
            log.write("[dim]ok[/]" if ev.ok else "[red]error[/]")

    def _refresh(self) -> None:
        table = self.query_one("#contacts", DataTable)
        table.clear()
        nearest = None
        for c in sorted(self.model.contacts.values(),
                        key=lambda c: (c.last_distance_cm is None,
                                       c.last_distance_cm or 0)):
            kind = "sniff" if c.passive else "range"
            dist = str(c.last_distance_cm) if c.last_distance_cm is not None else "-"
            rssi = f"{c.last_rssi_dbm:.0f}" if c.last_rssi_dbm is not None else "-"
            table.add_row(c.addr, kind, dist, str(c.samples or c.frames), rssi)
            if nearest is None and c.last_distance_cm is not None:
                nearest = c
        s = self.model.stats()
        self.query_one("#statline", Static).update(
            f"[b]mode[/] {self.mode}   "
            f"[b]contacts[/] {s['contacts']}   "
            f"[b]frames[/] {s['frames']}   "
            f"[b]ranges[/] {s['ranges']}   "
            f"[b]last rsl[/] {self.model.last_rssi_dbm}")
        if nearest and nearest.distance_history:
            self.query_one("#spark", Sparkline).data = list(nearest.distance_history)

    # --- key actions ---
    def action_listen(self) -> None:
        self.device.start_listener(); self.mode = "LISTENER"

    def action_initf(self) -> None:
        self.device.start_ranging("initf"); self.mode = "INITF"

    def action_respf(self) -> None:
        self.device.start_ranging("respf"); self.mode = "RESPF"

    def action_stop(self) -> None:
        self.device.stop(); self.mode = "STOP"

    def action_chan5(self) -> None:
        self.device.stop(); self.device.set_channel(5)
        self.query_one("#log", RichLog).write("[blue]channel -> 5[/]")

    def action_chan9(self) -> None:
        self.device.stop(); self.device.set_channel(9)
        self.query_one("#log", RichLog).write("[blue]channel -> 9[/]")

    def action_clear(self) -> None:
        self.model = RadarModel()

    def on_unmount(self) -> None:
        try:
            if self.device:
                self.device.stop()
            if self._ser:
                self._ser.close()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=None)
    args = ap.parse_args(argv)
    port = args.port or find_cli_port()
    if port is None:
        print("No CLI port. Plug a cable into the board's J20 (native USB).")
        return 1
    UwbExplorerApp(port).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
