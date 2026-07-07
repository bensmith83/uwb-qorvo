"""Phone-facing web dashboard for the portable UWB explorer.

A stdlib HTTP server (no extra deps — friendly to a Pi Zero) that serves a
single self-contained page and a `/api/state` JSON endpoint. A background
thread drives the board: opens the CLI console, starts the LISTENER, and polls
the PHY counters into a `DetectorState`. The page polls the JSON a couple times
a second and renders a live UWB "Geiger counter".

Run:  ./venv/bin/python -m uwb_explorer.web [--port 8080] [--sweep]
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .webmodel import DetectorState


def poll_once(device, state: DetectorState) -> dict:
    """One poll step: read LSTAT counters and fold them into the state.

    Isolated from serial/HTTP so it can be unit-tested with a fake device.
    A device that returns None (transient CLI hiccup) is treated as no news.
    """
    lstat = device.get_lstat() or {}
    return state.update(lstat)


class DashboardServer:
    """Serves the page and the state JSON. `snapshot` is a zero-arg callable
    returning the current JSON-able state dict."""

    def __init__(self, snapshot, host: str = "0.0.0.0", port: int = 8080):
        self._snapshot = snapshot
        handler = self._make_handler()
        self._httpd = ThreadingHTTPServer((host, port), handler)

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    def serve_forever(self) -> None:
        self._httpd.serve_forever()

    def shutdown(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def _make_handler(self):
        snapshot = self._snapshot

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence per-request stderr noise
                pass

            def _send(self, code, body: bytes, ctype: str):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path in ("/", "/index.html"):
                    self._send(200, PAGE.encode(), "text/html; charset=utf-8")
                elif self.path.startswith("/api/state"):
                    body = json.dumps(snapshot()).encode()
                    self._send(200, body, "application/json")
                else:
                    self._send(404, b"not found", "text/plain")

        return Handler


def board_loop(state: DetectorState, stop: threading.Event,
               sweep: bool = False, interval: float = 1.0,
               codes=(9, 10, 11, 12)) -> None:
    """Keep a board listening and fold its counters into `state` forever.

    Retries connecting so you can boot the unit and plug the board in later
    (the con workflow). Optional preamble-code sweep widens what it can hear.
    Imports serial bits lazily so the HTTP layer stays importable/testable on
    a host with no pyserial hardware access.
    """
    from .device import Device, _UWBCFG_ORDER
    from .serialport import find_cli_port, open_cli

    while not stop.is_set():
        port = find_cli_port()
        if not port:
            state.set_status("waiting")
            stop.wait(1.5)
            continue
        try:
            ser = open_cli(port)
            ser.setDTR(True)
            time.sleep(0.4)
            ser.reset_input_buffer()
            dev = Device(ser)
            if not dev.detect():
                state.set_status("error")
                ser.close()
                stop.wait(1.5)
                continue
            cfg = dev.get_uwbcfg() or {}
            state.set_config(channel=cfg.get("CHAN"), pcode=cfg.get("TXCODE"))
            state.set_status("live")
            code_cycle = list(codes) if sweep else [cfg.get("TXCODE", 9)]
            ci = 0
            dev.stop()
            dev.start_listener()
            while not stop.is_set():
                if sweep and len(code_cycle) > 1:
                    ci = (ci + 1) % len(code_cycle)
                    code = code_cycle[ci]
                    dev.stop()
                    p = dev.get_uwbcfg() or {}
                    p["TXCODE"] = code
                    p["RXCODE"] = code
                    dev.session.send("uwbcfg " + " ".join(str(p[k]) for k in _UWBCFG_ORDER))
                    time.sleep(0.2)
                    dev.start_listener()
                    state.set_config(pcode=code)
                poll_once(dev, state)
                stop.wait(interval)
            dev.stop()
            ser.close()
        except Exception:  # a serial wedge/reenumerate: back off and reconnect
            state.set_status("error")
            stop.wait(1.5)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Portable UWB explorer web dashboard")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--interval", type=float, default=1.0,
                    help="seconds between board polls")
    ap.add_argument("--sweep", action="store_true",
                    help="cycle preamble codes 9-12 to hear more device types")
    args = ap.parse_args(argv)

    state = DetectorState()
    stop = threading.Event()
    t = threading.Thread(target=board_loop, args=(state, stop),
                         kwargs={"sweep": args.sweep, "interval": args.interval},
                         daemon=True)
    t.start()

    srv = DashboardServer(state.snapshot, host=args.host, port=args.port)
    print(f"UWB dashboard on http://{args.host}:{args.port}  (Ctrl-C to stop)",
          file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        srv.shutdown()
    return 0


# --- self-contained phone page (no external requests; works on the con hotspot)
PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark light">
<title>UWB Explorer</title>
<style>
  :root{
    --bg:#0b0f14; --fg:#e8eef5; --muted:#7c8a9c; --card:#141b24;
    --idle:#3a4757; --low:#2f7d5b; --medium:#c99a2e; --high:#e0523d; --accent:#4aa8ff;
  }
  @media (prefers-color-scheme: light){
    :root{ --bg:#f3f5f8; --fg:#12181f; --muted:#5a6672; --card:#ffffff; --idle:#c2ccd6; }
  }
  *{ box-sizing:border-box; }
  html,body{ margin:0; height:100%; }
  body{
    background:var(--bg); color:var(--fg); font:16px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
    -webkit-font-smoothing:antialiased; padding:env(safe-area-inset-top) 16px env(safe-area-inset-bottom);
    display:flex; flex-direction:column; gap:14px; max-width:520px; margin:0 auto;
  }
  header{ display:flex; align-items:baseline; justify-content:space-between; padding-top:18px; }
  h1{ font-size:19px; margin:0; letter-spacing:.02em; }
  .status{ font-size:12px; color:var(--muted); text-transform:uppercase; letter-spacing:.08em; }
  .dot{ display:inline-block; width:8px; height:8px; border-radius:50%; background:var(--idle); margin-right:6px; vertical-align:middle; }
  .live .dot{ background:var(--low); box-shadow:0 0 8px var(--low); }
  .error .dot{ background:var(--high); }
  .meter{
    background:var(--card); border-radius:20px; padding:26px 22px; text-align:center;
    transition:box-shadow .25s, background .25s; position:relative; overflow:hidden;
  }
  .meter .lvl{ font-size:13px; letter-spacing:.14em; text-transform:uppercase; color:var(--muted); }
  .meter .big{ font-size:74px; font-weight:800; line-height:1; margin:8px 0 2px; font-variant-numeric:tabular-nums; }
  .meter .unit{ font-size:13px; color:var(--muted); }
  .meter.low{ box-shadow:inset 0 0 0 2px var(--low), 0 0 40px -18px var(--low); }
  .meter.medium{ box-shadow:inset 0 0 0 2px var(--medium), 0 0 60px -14px var(--medium); }
  .meter.high{ box-shadow:inset 0 0 0 2px var(--high), 0 0 90px -6px var(--high); animation:pulse .7s ease-in-out infinite; }
  @keyframes pulse{ 50%{ box-shadow:inset 0 0 0 2px var(--high), 0 0 120px 0 var(--high); } }
  canvas{ width:100%; height:70px; display:block; background:var(--card); border-radius:14px; }
  .grid{ display:grid; grid-template-columns:1fr 1fr; gap:10px; }
  .cell{ background:var(--card); border-radius:14px; padding:12px 14px; }
  .cell .k{ font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:.08em; }
  .cell .v{ font-size:24px; font-weight:700; font-variant-numeric:tabular-nums; }
  .note{ font-size:12px; color:var(--muted); text-align:center; }
  .decoded{ color:var(--low); font-weight:700; }
</style>
</head>
<body>
  <header>
    <h1>UWB&nbsp;Explorer</h1>
    <div class="status" id="status"><span class="dot"></span><span id="statusText">connecting</span></div>
  </header>

  <div class="meter" id="meter">
    <div class="lvl" id="level">idle</div>
    <div class="big" id="hits">0</div>
    <div class="unit">UWB frame-events / sec</div>
  </div>

  <canvas id="spark" width="480" height="70" aria-label="activity history"></canvas>

  <div class="grid">
    <div class="cell"><div class="k">Total heard</div><div class="v" id="total">0</div></div>
    <div class="cell"><div class="k">Peak /poll</div><div class="v" id="peak">0</div></div>
    <div class="cell"><div class="k">Channel</div><div class="v" id="channel">–</div></div>
    <div class="cell"><div class="k">Preamble</div><div class="v" id="pcode">–</div></div>
  </div>

  <div class="note" id="note">Point it at a car, a phone precision-finding, or an AirTag.</div>

<script>
(function(){
  var LEVELS = {idle:"idle", low:"faint", medium:"active", high:"STRONG"};
  var meter = document.getElementById("meter");
  var cv = document.getElementById("spark"), ctx = cv.getContext("2d");
  function css(v){ return getComputedStyle(document.documentElement).getPropertyValue(v).trim(); }

  function drawSpark(hist){
    var w = cv.width, h = cv.height; ctx.clearRect(0,0,w,h);
    if(!hist || !hist.length) return;
    var max = Math.max(1, Math.max.apply(null, hist));
    var n = hist.length, bw = w / n;
    for(var i=0;i<n;i++){
      var v = hist[i]/max, bh = Math.max(1, v*(h-6));
      var col = v>0.6? css("--high") : v>0.2? css("--medium") : v>0? css("--low") : css("--idle");
      ctx.fillStyle = col;
      ctx.fillRect(i*bw+1, h-bh, Math.max(1,bw-2), bh);
    }
  }

  function tick(){
    fetch("/api/state").then(function(r){return r.json();}).then(function(s){
      var st = s.status || "waiting";
      var statusEl = document.getElementById("status");
      statusEl.className = "status " + st;
      document.getElementById("statusText").textContent =
        st==="live" ? "live" : st==="error" ? "board error" : "waiting for board";

      var lvl = s.level || "idle";
      meter.className = "meter " + lvl;
      document.getElementById("level").textContent = LEVELS[lvl] || lvl;
      document.getElementById("hits").textContent = s.hits||0;
      document.getElementById("total").textContent = s.total||0;
      document.getElementById("peak").textContent = s.peak||0;
      document.getElementById("channel").textContent = s.channel==null? "–" : s.channel;
      document.getElementById("pcode").textContent = s.pcode==null? "–" : s.pcode;
      drawSpark(s.history);

      var note = document.getElementById("note");
      if(st==="waiting"){ note.textContent = "Plug the DWM3001CDK into the Pi (J20)…"; }
      else if(s.decoded){ note.innerHTML = '<span class="decoded">'+s.decoded+' frame(s) fully decoded ✓</span>'; }
      else if((s.hits||0)>0){ note.textContent = "UWB energy detected — frames hitting the antenna."; }
      else { note.textContent = "Point it at a car, a phone precision-finding, or an AirTag."; }
    }).catch(function(){
      document.getElementById("statusText").textContent = "reconnecting";
    });
  }
  setInterval(tick, 500); tick();
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
