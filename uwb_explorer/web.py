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

from .experiments import control
from .webmodel import DetectorState


def poll_once(device, state: DetectorState) -> dict:
    """One poll step: read LSTAT counters and fold them into the state.

    Isolated from serial/HTTP so it can be unit-tested with a fake device.
    A device that returns None (transient CLI hiccup) is treated as no news.
    """
    lstat = device.get_lstat() or {}
    return state.update(lstat)


def listener_step(dev, state: DetectorState, arbiter):
    """One board_loop listener iteration, gated by the port arbiter.

    When an experiment holds the port (``arbiter.is_active()``) this touches NO
    device method and returns None, so the passive listener stays off the single
    serial port. Otherwise it polls as usual, holding the arbiter's device lock
    for the duration of the poll so a starting experiment's transition barrier
    can serialize against an in-flight poll (the flag alone leaves a residual
    overlap window at the Start transition). ``arbiter`` may be None (no
    arbitration configured), in which case it always polls without a lock. Only
    ``is_active()`` and ``device()`` are called on the arbiter, so ``web`` needs
    no import of the arbiter class.
    """
    if arbiter is None:
        return poll_once(dev, state)
    if arbiter.is_active():
        return None
    with arbiter.device():
        return poll_once(dev, state)


class DashboardServer:
    """Serves the page and the state JSON. `snapshot` is a zero-arg callable
    returning the current JSON-able state dict."""

    def __init__(self, snapshot, host: str = "0.0.0.0", port: int = 8080,
                 dispatcher=None):
        self._snapshot = snapshot
        self._dispatcher = dispatcher   # control.Dispatcher (the experiment downlink) or None
        self._running = None            # letter of the currently-running experiment
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
        server = self  # closure onto the DashboardServer for the experiment downlink

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

            def _send_json(self, code, obj):
                self._send(code, json.dumps(obj).encode(), "application/json")

            def do_GET(self):
                if self.path in ("/", "/index.html"):
                    self._send(200, PAGE.encode(), "text/html; charset=utf-8")
                elif self.path == "/api/experiment/status":
                    self._send_json(200, {"running": server._running})
                elif self.path.startswith("/api/state"):
                    body = json.dumps(snapshot()).encode()
                    self._send(200, body, "application/json")
                else:
                    self._send(404, b"not found", "text/plain")

            def do_POST(self):
                if self.path != "/api/experiment":
                    self._send(404, b"not found", "text/plain")
                    return

                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    payload = json.loads(raw or b"{}")
                    opcode = payload["opcode"]
                except (ValueError, KeyError, TypeError) as e:
                    self._send_json(400, {"ok": False, "error": f"bad request: {e}"})
                    return

                if server._dispatcher is None:
                    self._send_json(503, {"ok": False,
                                          "error": "no experiment dispatcher configured"})
                    return

                try:
                    cmd = control.parse_command(opcode)
                except ValueError as e:
                    self._send_json(400, {"ok": False, "error": str(e)})
                    return

                result = server._dispatcher.dispatch(cmd)
                if cmd.action == "start":
                    server._running = cmd.exp
                elif cmd.action == "stop":
                    server._running = None
                self._send_json(200, {"ok": True, "result": result})

        return Handler


def recover_arbitration(arbiter) -> None:
    """Reset port arbitration to a clean slate after a serial error mid-loop.

    A board that USB re-enumerates DURING an active experiment throws a serial
    exception out of the pump/poll while the arbiter is still ACTIVE and its
    quiesce handshake is half-armed. Left as-is, the rebuilt dispatcher and the
    passive listener would stay wedged off the port (``is_active()`` still True)
    until an explicit stop. Releasing here lets the reconnect start fresh: the
    active flag is cleared, the listener is marked down (its device is gone), and
    the quiesce wait is fired so a subsequent start doesn't block on a handshake
    that will never complete. Idempotent and safe when ``arbiter`` is None or
    already inactive; only touches the arbiter, so ``web`` needs no import of it.
    """
    if arbiter is None:
        return
    arbiter.resume()                     # deactivate a wedged-active arbiter
    arbiter.set_listener_running(False)  # our listener is down; device is gone
    arbiter.mark_quiesced()              # clear any half-armed quiesce wait


def board_loop(state: DetectorState, stop: threading.Event,
               sweep: bool = False, interval: float = 1.0,
               codes=(9, 10, 11, 12), on_connect=None, arbiter=None,
               pump=None) -> None:
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
            # hand the live device to any experiment downlink (e.g. the scanner
            # controller). Port arbitration with this listener loop is handled by
            # the `arbiter`: the flag keeps this loop out for the experiment's
            # window; the device lock (held per-poll by listener_step) lets a
            # starting experiment barrier against an in-flight poll; and the
            # quiesce handshake (set_listener_running / mark_quiesced below) makes
            # the controller's start WAIT until this loop has stopped its listener
            # and is off the port, so a late board stop can't kill the experiment.
            if on_connect:
                on_connect(dev)
            code_cycle = list(codes) if sweep else [cfg.get("TXCODE", 9)]
            ci = 0
            paused = False   # have we handed the port to an active experiment?
            dev.stop()
            dev.start_listener()
            if arbiter is not None:
                arbiter.set_listener_running(True)
            while not stop.is_set():
                # half-duplex handoff via the quiesce handshake: when an
                # experiment goes active, stop our listener ONCE so the board is
                # idle for it, then tell the arbiter we're OFF the port
                # (set_listener_running(False) + mark_quiesced). The controller's
                # start is BLOCKED in wait_quiesced until this mark, so our
                # dev.stop() here provably lands BEFORE the controller drives the
                # port — it can no longer stop the port after the experiment has
                # started and kill it. Don't touch the device again until the
                # experiment releases; resume the listener when it goes inactive.
                if arbiter is not None and arbiter.is_active():
                    if not paused:
                        dev.stop()
                        arbiter.set_listener_running(False)
                        arbiter.mark_quiesced()
                        paused = True
                    # drive the active experiment's sweep forward one combo per
                    # iteration (bug nmr): the board thread owns the port now, so
                    # it pumps step() under the device lock. Without this the
                    # sweep stalled on combo 0 (start ran it, nothing advanced).
                    if pump is not None:
                        pump()
                    stop.wait(interval)
                    continue
                if paused:
                    dev.start_listener()
                    if arbiter is not None:
                        arbiter.set_listener_running(True)
                    paused = False
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
                listener_step(dev, state, arbiter)
                stop.wait(interval)
            dev.stop()
            ser.close()
        except Exception:  # a serial wedge/reenumerate: back off and reconnect
            state.set_status("error")
            # If a board re-enumerated mid-experiment the arbiter is still ACTIVE
            # and its quiesce handshake half-armed; release it so the rebuilt
            # dispatcher and the passive listener resume from a clean slate rather
            # than staying wedged off the port until an explicit stop (bead 0ux).
            recover_arbitration(arbiter)
            stop.wait(1.5)
        finally:
            if on_connect:
                on_connect(None)  # device is gone; drop the downlink's reference


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
  #experiments{ display:flex; flex-direction:column; gap:10px; }
  .exp-h{ font-size:15px; margin:4px 0 0; letter-spacing:.02em; }
  .exp-sub{ font-size:12px; color:var(--muted); font-weight:400; }
  .exp-card{ display:flex; flex-direction:column; gap:10px; }
  .exp-row{ display:flex; align-items:center; gap:10px; }
  .btn{
    background:var(--accent); color:#04121f; border:0; border-radius:10px;
    padding:9px 14px; font-size:14px; font-weight:700; cursor:pointer;
  }
  .btn-off{ background:var(--idle); color:var(--fg); }
  .exp-state{ font-size:12px; color:var(--muted); text-transform:uppercase; letter-spacing:.08em; margin-left:auto; }
  .exp-prog{ font-size:13px; color:var(--muted); font-variant-numeric:tabular-nums; }
  .exp-list{ list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:6px; }
  .exp-list li{ font-size:13px; font-variant-numeric:tabular-nums; display:flex; justify-content:space-between; gap:8px; }
  .exp-list .addr{ font-weight:700; }
  .exp-list .where{ color:var(--muted); }
  .btn-danger{ background:var(--high); color:#fff; }
  .exp-warn{
    font-size:12px; font-weight:700; letter-spacing:.02em; color:var(--high);
    border:1.5px solid var(--high); border-radius:10px; padding:8px 10px;
  }
  .exp-select{
    background:var(--idle); color:var(--fg); border:0; border-radius:10px;
    padding:9px 10px; font-size:14px;
  }
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

  <section id="experiments">
    <h2 class="exp-h">Scanner <span class="exp-sub">— the nmap for UWB</span></h2>
    <div class="cell exp-card">
      <div class="exp-row">
        <button id="scanStart" class="btn">Start scan</button>
        <button id="scanStop" class="btn btn-off">Stop</button>
        <span class="exp-state" id="scanState">idle</span>
      </div>
      <div class="exp-prog"><span id="scanStep">0</span>/<span id="scanTotal">0</span> combos swept</div>
      <ul class="exp-list" id="scanDevices"></ul>
    </div>

    <h2 class="exp-h">Transponder <span class="exp-sub">— a discoverable UWB landmark</span></h2>
    <div class="cell exp-card">
      <div class="exp-row">
        <button id="respStart" class="btn">Start transponder</button>
        <button id="respStop" class="btn btn-off">Stop</button>
        <span class="exp-state" id="respState">idle</span>
      </div>
      <div class="exp-prog"><span id="respStep">0</span>/<span id="respTotal">0</span> combos answered</div>
      <ul class="exp-list" id="respAnswered"></ul>
    </div>

    <h2 class="exp-h">Fuzzer <span class="exp-sub">— fire a malformed frame, listen for a reaction</span></h2>
    <div class="cell exp-card">
      <div class="exp-warn" id="fuzzWarn">
        Authorized targets / own devices only. Fire fuzz cases ONLY at UWB
        hardware you own or are explicitly authorized to test — never point
        this at infrastructure or third-party devices. Each Fire is one
        deliberate, manually-triggered frame — nothing here auto-fires.
      </div>
      <div class="exp-row">
        <select id="fuzzCase" class="exp-select">
          <option value="bad-crc">bad-crc</option>
          <option value="invalid-frametype">invalid-frametype</option>
          <option value="oversized-phr">oversized-phr</option>
          <option value="truncated-mac">truncated-mac</option>
          <option value="illegal-sts">illegal-sts</option>
        </select>
        <button id="fuzzFire" class="btn btn-danger">Fire</button>
        <button id="fuzzStop" class="btn btn-off">Stop</button>
        <span class="exp-state" id="fuzzState">idle</span>
      </div>
      <div class="exp-prog">last case: <span id="fuzzLastCase">–</span></div>
      <ul class="exp-list" id="fuzzReactions"></ul>
    </div>
  </section>

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

  // --- Scanner experiment: drive the downlink and poll its progress ---
  function postExp(opcode){
    return fetch("/api/experiment", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({opcode:opcode})
    }).then(function(r){ return r.json(); });
  }
  var scanState = document.getElementById("scanState");
  var scanStep = document.getElementById("scanStep");
  var scanTotal = document.getElementById("scanTotal");
  var scanDevices = document.getElementById("scanDevices");
  document.getElementById("scanStart").addEventListener("click", function(){
    // start the PHY sweep (default channels x preamble codes). Opcode args are
    // comma-separated key=value pairs, so a LIST value uses ';' as its
    // sub-delimiter (a ',' inside a value would be rejected by parse_command).
    postExp("XS1 channels=5;9,pcodes=9;10;11;12")
      .catch(function(){ scanState.textContent = "error"; });
  });
  document.getElementById("scanStop").addEventListener("click", function(){
    postExp("XS0").catch(function(){ scanState.textContent = "error"; });
  });
  function renderScan(st){
    if(!st){ return; }
    scanState.textContent = st.running ? "scanning" : "idle";
    scanStep.textContent = st.step==null? 0 : st.step;
    scanTotal.textContent = st.total==null? 0 : st.total;
    var devs = st.devices || [];
    scanDevices.innerHTML = "";
    devs.forEach(function(d){
      var li = document.createElement("li");
      var a = document.createElement("span"); a.className = "addr"; a.textContent = d.addr;
      var w = document.createElement("span"); w.className = "where";
      w.textContent = "ch"+d.channel+" p"+d.pcode+" ×"+d.reply_count;
      li.appendChild(a); li.appendChild(w); scanDevices.appendChild(li);
    });
  }
  function scanTick(){
    // /api/experiment/status tells us which experiment (if any) is running;
    // pull the scanner's detailed sweep progress via its status opcode.
    fetch("/api/experiment/status").then(function(r){ return r.json(); }).then(function(s){
      if(s.running === "S"){
        postExp("XS?").then(function(p){ renderScan(p && p.result); });
      } else {
        scanState.textContent = "idle";
      }
    }).catch(function(){});
  }
  setInterval(scanTick, 1000); scanTick();

  // --- Transponder experiment: answer polls across the PHY space ---
  var respState = document.getElementById("respState");
  var respStep = document.getElementById("respStep");
  var respTotal = document.getElementById("respTotal");
  var respAnswered = document.getElementById("respAnswered");
  document.getElementById("respStart").addEventListener("click", function(){
    // answer polls across the default channels x preamble codes. As with the
    // scanner, a LIST value uses ';' as its sub-delimiter because opcode args
    // are comma-separated key=value pairs (',' inside a value is rejected).
    postExp("XT1 channels=5;9,pcodes=9;10;11;12")
      .catch(function(){ respState.textContent = "error"; });
  });
  document.getElementById("respStop").addEventListener("click", function(){
    postExp("XT0").catch(function(){ respState.textContent = "error"; });
  });
  function renderResp(st){
    if(!st){ return; }
    respState.textContent = st.running ? "answering" : "idle";
    respStep.textContent = st.step==null? 0 : st.step;
    respTotal.textContent = st.total==null? 0 : st.total;
    var polls = st.answered || [];
    respAnswered.innerHTML = "";
    polls.forEach(function(d){
      var li = document.createElement("li");
      var a = document.createElement("span"); a.className = "addr"; a.textContent = d.addr;
      var w = document.createElement("span"); w.className = "where";
      w.textContent = "ch"+d.channel+" p"+d.pcode+" ×"+d.poll_count;
      li.appendChild(a); li.appendChild(w); respAnswered.appendChild(li);
    });
  }
  function respTick(){
    fetch("/api/experiment/status").then(function(r){ return r.json(); }).then(function(s){
      if(s.running === "T"){
        postExp("XT?").then(function(p){ renderResp(p && p.result); });
      } else {
        respState.textContent = "idle";
      }
    }).catch(function(){});
  }
  setInterval(respTick, 1000); respTick();

  // --- Fuzzer experiment: ONE deliberate malformed-frame fire, then listen ---
  // AUTHORIZED TARGETS / OWN DEVICES ONLY — see the always-visible warning in
  // the panel above. Fire is a single manual button press; nothing here
  // auto-fires or repeats on its own.
  var fuzzState = document.getElementById("fuzzState");
  var fuzzLastCase = document.getElementById("fuzzLastCase");
  var fuzzReactions = document.getElementById("fuzzReactions");
  var fuzzCase = document.getElementById("fuzzCase");
  document.getElementById("fuzzFire").addEventListener("click", function(){
    postExp("XZ1 case=" + fuzzCase.value)
      .catch(function(){ fuzzState.textContent = "error"; });
  });
  document.getElementById("fuzzStop").addEventListener("click", function(){
    postExp("XZ0").catch(function(){ fuzzState.textContent = "error"; });
  });
  function renderFuzz(st){
    if(!st){ return; }
    fuzzState.textContent = st.running ? "fired" : "idle";
    fuzzLastCase.textContent = st.last_case || "–";
    var reactions = st.reactions || [];
    fuzzReactions.innerHTML = "";
    reactions.forEach(function(r){
      var li = document.createElement("li");
      var a = document.createElement("span"); a.className = "addr"; a.textContent = r.type;
      var w = document.createElement("span"); w.className = "where";
      w.textContent = r.payload ? ("payload="+r.payload) : ("t="+r.timestamp);
      li.appendChild(a); li.appendChild(w); fuzzReactions.appendChild(li);
    });
  }
  function fuzzTick(){
    fetch("/api/experiment/status").then(function(r){ return r.json(); }).then(function(s){
      if(s.running === "Z"){
        postExp("XZ?").then(function(p){ renderFuzz(p && p.result); });
      } else {
        fuzzState.textContent = "idle";
      }
    }).catch(function(){});
  }
  setInterval(fuzzTick, 1000); fuzzTick();
})();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
