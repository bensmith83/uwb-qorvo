"""Half-duplex port arbitration for the single serial board — bead uwb-qorvo-1hu.21.

``web.board_loop`` drives the passive LISTENER on the board's one serial port from
the board thread, while an HTTP ``POST /api/experiment`` drives an active
experiment (scanner/transponder) on the SAME port from the HTTP handler thread.
Two threads, one port -> corruption. ``PortArbiter`` is the tiny hardware-free
primitive both sides consult so the listener and an active experiment never touch
the port at once:

  * an "active" boolean flag (idempotent ``pause``/``resume``, NOT a refcount)
    that the listener reads via ``is_active()`` to yield the port;
  * a single device ``Lock`` giving EXCLUSIVE access (``device()`` /
    ``try_acquire()`` / ``release()``); and
  * a "quiesced" handshake ``Event`` that lets a starting experiment WAIT for the
    board to actually stop its listener and get off the port before the
    controller drives it (``pause`` arms it, ``mark_quiesced`` fires it,
    ``wait_quiesced`` blocks on it).

The flag and the device lock are INDEPENDENT: the flag coordinates *whose turn*
it is; the lock enforces *one owner at a time*. The active flag alone is not
enough at the Start transition: the board sees the flag and stops its listener,
but that stop runs CONCURRENTLY with the controller's start — the board's
``dev.stop()`` could land AFTER the controller's ``start_initf`` and kill the
just-started experiment. The quiesce handshake closes that window: ``pause``
clears ``_quiesced`` (only if a listener is actually running), the board calls
``mark_quiesced`` right AFTER it has stopped its listener, and the controller's
start blocks in ``wait_quiesced`` until then — so the board's stop provably
precedes the controller driving the port. ``ArbitratedDispatcher`` wraps the
downlink to sequence all of this: a "start" pauses, waits for quiesce, then
drives the controller; a "stop" resumes only AFTER it releases the port.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager


class PortArbiter:
    """A boolean "experiment active" flag, one exclusive device lock, and a
    quiesce handshake so a starting experiment waits for the board to get off
    the port.

    Thread-safe by construction: ``pause``/``resume`` (HTTP thread) and
    ``is_active`` (board thread) touch only a small flag lock, never the device
    lock, so consulting the flag can never deadlock against a held port. The
    quiesce ``Event`` is a stdlib primitive, safe to set/clear/wait across
    threads without extra locking.
    """

    def __init__(self, active: bool = False):
        self._active = active
        self._flag_lock = threading.Lock()   # guards active + listener_running
        self._dev_lock = threading.Lock()    # the single exclusive device lock
        # quiesce handshake. INITIALLY SET: with no board listener running there
        # is nothing to quiesce, so wait_quiesced returns immediately and the
        # start doesn't block (keeps listener-free unit tests fast).
        self._quiesced = threading.Event()
        self._quiesced.set()
        self._listener_running = False       # is board_loop's listener up?

    def is_active(self) -> bool:
        with self._flag_lock:
            return self._active

    def set_listener_running(self, running: bool) -> None:
        """board_loop reports whether its passive listener is currently up.

        Only when a listener is up does ``pause`` have something to wait for; if
        no listener is running the quiesce handshake is a no-op (stays set).
        """
        with self._flag_lock:
            self._listener_running = running

    def pause(self) -> None:
        """Mark an experiment active and, IF a board listener is up, arm the
        quiesce wait so the controller's start blocks until the board stops it.

        Idempotent boolean (not a counter). If no listener is running there is
        nothing to quiesce, so ``_quiesced`` is left SET and the start won't
        block."""
        with self._flag_lock:
            self._active = True
            if self._listener_running:
                self._quiesced.clear()

    def mark_quiesced(self) -> None:
        """board_loop calls this right AFTER it has stopped its listener and will
        not touch the port for the experiment's window; releases wait_quiesced."""
        self._quiesced.set()

    def wait_quiesced(self, timeout: float) -> bool:
        """Block until the board has quiesced (or ``timeout``). True iff quiesced.

        Returns immediately True when nothing armed the wait (no listener), so a
        start with no board never blocks."""
        return self._quiesced.wait(timeout)

    def resume(self) -> None:
        """Mark no experiment active; a lone resume is a harmless no-op.

        Leaves ``_quiesced`` as-is: it is irrelevant while inactive and the next
        ``pause`` re-arms it if a listener is up by then."""
        with self._flag_lock:
            self._active = False

    @contextmanager
    def device(self):
        """Exclusive access to the device: blocks until the port is free."""
        self._dev_lock.acquire()
        try:
            yield
        finally:
            self._dev_lock.release()

    def try_acquire(self, timeout: float = 0.0) -> bool:
        """Non-blocking-by-default acquire of the device lock; True iff taken."""
        if timeout > 0:
            return self._dev_lock.acquire(blocking=True, timeout=timeout)
        return self._dev_lock.acquire(blocking=False)

    def release(self) -> None:
        """Release the device lock taken by ``try_acquire``."""
        self._dev_lock.release()


class ArbitratedDispatcher:
    """Wraps the downlink so start/stop pause/resume the arbiter around the port.

    A "start" (1) pauses the arbiter — which also arms the quiesce wait if a
    board listener is up; (2) WAITS in ``wait_quiesced`` until the board has
    stopped its listener and called ``mark_quiesced`` (so the board's
    ``dev.stop()`` provably precedes the controller driving the port — the race
    where a late board stop killed the just-started experiment is closed); (3)
    crosses a device-lock barrier to drain any in-flight listener poll; then (4)
    drives the inner controller. If the quiesce wait times out (a wedged/absent
    board that never marks quiesced) the start proceeds best-effort rather than
    hanging — the documented degraded case, not a wedge.

    A "stop" runs the inner controller FIRST and resumes only AFTER it returns
    (via try/finally, so a raising controller still resumes rather than wedging
    the listener off the port). A "status" query touches neither.

    ``start_timeout`` bounds the quiesce wait (default 2.0s; tests inject a tiny
    value).
    """

    def __init__(self, inner, arbiter: PortArbiter, start_timeout: float = 2.0):
        self._inner = inner
        self._arbiter = arbiter
        self._start_timeout = start_timeout
        # the controller a start drove, IFF it can be pumped (has step()). The
        # board thread advances a multi-combo sweep by calling pump(); a stop or
        # exhaustion clears it. Plain attribute: reads/writes are atomic under
        # the GIL, and the device lock orders the pump/stop critical sections.
        self._active_ctrl = None

    def _steppable(self, exp):
        """The controller for ``exp`` if it exposes a callable step(), else None."""
        getter = getattr(self._inner, "controller_for", None)
        if getter is None:
            return None
        ctrl = getter(exp)
        return ctrl if callable(getattr(ctrl, "step", None)) else None

    def pump(self) -> bool:
        """Advance the active experiment one step under the device lock.

        Returns True if it stepped and more work remains, False if there is
        nothing to pump (no active experiment, not steppable, or exhausted). The
        board thread calls this each idle iteration while an experiment is active,
        holding the device lock so a step never races a listener poll. A
        concurrent stop() clears ``_active_ctrl`` under the same lock, so a step
        can never run after (or during) the controller being stopped.
        """
        if self._active_ctrl is None:
            return False
        with self._arbiter.device():
            ctrl = self._active_ctrl        # re-read under the lock (stop clears it)
            if ctrl is None:
                return False
            more = bool(ctrl.step())
        if not more:
            self._active_ctrl = None        # sweep exhausted: stop pumping
        return more

    def dispatch(self, cmd):
        if cmd.action == "start":
            self._arbiter.pause()
            # Wait for the board to quiesce: pause armed _quiesced (iff a listener
            # is up), and board_loop calls mark_quiesced only AFTER it has stopped
            # its listener. Blocking here until then GUARANTEES the board's
            # dev.stop() lands before the controller drives the port. With no
            # listener running this returns immediately (nothing to wait for). A
            # timeout means a wedged/absent board — proceed best-effort so a start
            # never hangs the system (degraded, documented).
            self._arbiter.wait_quiesced(self._start_timeout)
            # transition barrier: drain any in-flight listener poll (which holds
            # the device lock) before the controller drives the device, so the
            # start can never overlap a poll that was already on the port.
            with self._arbiter.device():
                pass
            # NOTE (finding #3): if inner.dispatch raises here, the arbiter stays
            # paused and server._running is left unset — a misleading "idle" UI
            # for a failed start. Low priority; left as-is.
            result = self._inner.dispatch(cmd)
            # start() ran combo 0; if the controller can step, arm the pump so the
            # board thread drives the rest of the sweep (bug nmr — previously the
            # sweep stalled on combo 0 because nothing ever called step()).
            self._active_ctrl = self._steppable(cmd.exp)
            return result
        if cmd.action == "stop":
            # ALWAYS resume, even if the inner controller raises — otherwise the
            # arbiter stays paused forever and the board listener is wedged off
            # the port. try/finally preserves the normal-path ordering: the
            # inner runs (and returns its value) while still active, and resume
            # runs only after. The stop (and the clear of the pump target) runs
            # UNDER the device lock so it can never overlap an in-flight pump step.
            try:
                with self._arbiter.device():
                    self._active_ctrl = None
                    return self._inner.dispatch(cmd)
            finally:
                self._arbiter.resume()
        return self._inner.dispatch(cmd)
