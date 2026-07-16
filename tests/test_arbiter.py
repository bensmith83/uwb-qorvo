"""TDD (RED) for half-duplex port arbitration — bead uwb-qorvo-1hu.21.

The problem: ``web.board_loop`` continuously drives the LISTENER + LSTAT on the
single serial ``Device``. When an HTTP ``POST /api/experiment`` dispatches an
experiment, a real ScannerController/TransponderController drives ``set_uwbcfg``
+ ``start_initf``/``start_respf`` on the SAME device from the HTTP handler
thread. Two threads, one serial port -> corruption. We need an arbiter both
sides consult so the passive listener and an active experiment NEVER touch the
port at once.

The tested seam is a small, hardware-free primitive plus two thin consult
points — deliberately unit-testable without a board or serial:

  * ``PortArbiter`` (uwb_explorer/experiments/arbiter.py): a threading.Lock +
    a boolean "experiment active" flag.
      - ``pause()`` / ``resume()`` set/clear the flag (idempotent booleans, NOT
        a counter); ``is_active()`` reflects it.
      - ``device()`` is a context manager giving EXCLUSIVE access via the lock;
        two acquisitions are mutually exclusive. ``try_acquire(timeout=0.0)`` /
        ``release()`` are the non-blocking form.
  * ``web.listener_step(dev, state, arbiter)``: the extracted per-iteration
    listener step. When ``arbiter.is_active()`` it does NOTHING and touches no
    device method; otherwise it polls (``poll_once``). ``board_loop`` gains an
    ``arbiter=`` kwarg and consults it via this step.
  * ``arbiter.ArbitratedDispatcher(inner, arbiter)``: the wrapper the downlink
    uses. A "start" pauses the arbiter BEFORE the controller drives the device;
    a "stop" resumes it AFTER the controller releases it; "status" leaves the
    arbiter untouched.

None of these symbols exist yet, so the imports/attribute lookups fail — that IS
the RED signal. Every arbiter/wrapper import is done INSIDE its test so a missing
module fails only that test, not the whole file's collection.
"""

from __future__ import annotations

import inspect
import threading
import time

from uwb_explorer import web  # module exists; listener_step/arbiter kwarg do not yet
from uwb_explorer.experiments.control import parse_command
from uwb_explorer.webmodel import DetectorState


class _CountingDev:
    """A fake device that records every serial-touching call the listener makes."""

    def __init__(self):
        self.calls: list[str] = []
        self.n = 0

    def get_lstat(self):
        self.calls.append("get_lstat")
        self.n += 1
        return {"SFDD": self.n * 5}


# --- PortArbiter: the active flag -------------------------------------------

def test_arbiter_starts_inactive():
    from uwb_explorer.experiments.arbiter import PortArbiter
    arb = PortArbiter()
    assert arb.is_active() is False


def test_pause_then_resume_toggles_active():
    from uwb_explorer.experiments.arbiter import PortArbiter
    arb = PortArbiter()
    arb.pause()
    assert arb.is_active() is True
    arb.resume()
    assert arb.is_active() is False


def test_double_pause_and_lone_resume_are_safe():
    # boolean flag semantics (NOT a refcount): a second pause keeps it active,
    # a single resume clears it, and a resume with no prior pause is a harmless
    # no-op — none of these corrupt the state or deadlock.
    from uwb_explorer.experiments.arbiter import PortArbiter
    arb = PortArbiter()
    arb.pause()
    arb.pause()
    assert arb.is_active() is True
    arb.resume()
    assert arb.is_active() is False
    arb.resume()  # lone resume, must not raise
    assert arb.is_active() is False


# --- PortArbiter: exclusive device access -----------------------------------

def test_device_lock_is_mutually_exclusive():
    from uwb_explorer.experiments.arbiter import PortArbiter
    arb = PortArbiter()
    assert arb.try_acquire() is True     # first caller takes the port
    assert arb.try_acquire() is False    # second is refused while held
    arb.release()
    assert arb.try_acquire() is True     # free again after release
    arb.release()


def test_second_device_acquire_blocks_until_release():
    # a second `with arb.device()` must BLOCK until the first releases, so the
    # listener can never slip onto the port mid-experiment.
    from uwb_explorer.experiments.arbiter import PortArbiter
    arb = PortArbiter()
    order: list[str] = []
    got_it = threading.Event()

    with arb.device():
        order.append("held")

        def worker():
            with arb.device():
                order.append("second")
                got_it.set()

        t = threading.Thread(target=worker)
        t.start()
        time.sleep(0.1)
        order.append("still-held")
        assert not got_it.is_set()   # blocked the whole time we held it

    t.join(timeout=2.0)
    assert got_it.is_set()           # proceeded once we released
    assert order == ["held", "still-held", "second"]


# --- listener_step: the board_loop consult point ----------------------------

def test_listener_step_skips_device_when_arbiter_active():
    from uwb_explorer.experiments.arbiter import PortArbiter
    arb = PortArbiter()
    arb.pause()                      # an experiment holds the port
    dev = _CountingDev()
    state = DetectorState()
    result = web.listener_step(dev, state, arb)
    assert result is None            # nothing polled
    assert dev.calls == []           # and the device was NOT touched


def test_listener_step_polls_when_arbiter_inactive():
    from uwb_explorer.experiments.arbiter import PortArbiter
    arb = PortArbiter()              # inactive
    dev = _CountingDev()
    state = DetectorState()
    web.listener_step(dev, state, arb)       # baseline
    snap = web.listener_step(dev, state, arb)  # +5 SFDD
    assert dev.calls == ["get_lstat", "get_lstat"]
    assert snap is not None
    assert snap["hits"] == 5


def test_board_loop_accepts_an_arbiter_param():
    # board_loop is hardware-bound, so we only assert the seam exists: it must
    # accept an `arbiter=` keyword for the listener side to consult.
    sig = inspect.signature(web.board_loop)
    assert "arbiter" in sig.parameters


# --- ArbitratedDispatcher: the downlink pauses/resumes around an experiment --

class _RecordingInner:
    """A fake inner dispatcher that records whether the arbiter was active at
    the instant the controller was actually driven."""

    def __init__(self, arbiter):
        self._arb = arbiter
        self.active_at_dispatch: list[bool] = []
        self.cmds = []

    def dispatch(self, cmd):
        self.cmds.append(cmd)
        self.active_at_dispatch.append(self._arb.is_active())
        return {"ok": True, "exp": cmd.exp, "action": cmd.action}


def test_arbitrated_dispatcher_pauses_before_start_reaches_controller():
    # a "start" must pause the arbiter BEFORE the controller touches the device,
    # so the listener has already yielded the port. Using the REAL arbiter, the
    # inner sees is_active()==True at dispatch time iff the pause preceded it.
    from uwb_explorer.experiments.arbiter import PortArbiter, ArbitratedDispatcher
    arb = PortArbiter()
    inner = _RecordingInner(arb)
    wrapped = ArbitratedDispatcher(inner, arb)

    result = wrapped.dispatch(parse_command("XS1"))
    assert inner.active_at_dispatch == [True]  # paused before the controller ran
    assert arb.is_active() is True             # stays active while it runs
    assert result == {"ok": True, "exp": "S", "action": "start"}


def test_arbitrated_dispatcher_resumes_after_stop_releases_controller():
    # a "stop" must resume the arbiter AFTER the controller has released the
    # port. With the REAL arbiter (already active), the inner still sees
    # is_active()==True at dispatch time; only after the wrapper returns is it
    # cleared.
    from uwb_explorer.experiments.arbiter import PortArbiter, ArbitratedDispatcher
    arb = PortArbiter()
    arb.pause()                       # an experiment is running
    inner = _RecordingInner(arb)
    wrapped = ArbitratedDispatcher(inner, arb)

    wrapped.dispatch(parse_command("XS0"))
    assert inner.active_at_dispatch == [True]  # still active while stopping
    assert arb.is_active() is False            # released only after it returned


class _RaisingInner:
    """A fake inner dispatcher that raises on dispatch, recording it happened."""

    def __init__(self, arbiter):
        self._arb = arbiter
        self.active_at_dispatch: list[bool] = []

    def dispatch(self, cmd):
        self.active_at_dispatch.append(self._arb.is_active())
        raise RuntimeError("controller blew up mid-stop")


def test_arbitrated_dispatcher_resumes_even_if_stop_raises():
    # REGRESSION: if the inner controller RAISES on a "stop", the arbiter must
    # still be resumed — otherwise the listener stays paused forever and the
    # board is wedged off the port. The exception must propagate, and afterwards
    # is_active() must be False.
    from uwb_explorer.experiments.arbiter import PortArbiter, ArbitratedDispatcher
    import pytest

    arb = PortArbiter()
    arb.pause()                       # an experiment is running
    inner = _RaisingInner(arb)
    wrapped = ArbitratedDispatcher(inner, arb)

    with pytest.raises(RuntimeError, match="blew up"):
        wrapped.dispatch(parse_command("XS0"))
    assert inner.active_at_dispatch == [True]  # still active while stopping
    assert arb.is_active() is False            # resumed despite the raise


def test_listener_step_holds_device_lock_while_polling():
    # FIX 2: the listener must poll UNDER the device lock, so an experiment's
    # start-transition barrier can serialize against an in-flight poll. While
    # the device is being read, the arbiter's device lock must be held.
    from uwb_explorer.experiments.arbiter import PortArbiter
    arb = PortArbiter()
    observed = {}

    class _LockObservingDev:
        def get_lstat(self):
            # if listener_step holds the device lock, a non-blocking acquire fails
            got = arb.try_acquire(0.0)
            if got:
                arb.release()
            observed["held"] = not got
            return {"SFDD": 5}

    state = DetectorState()
    web.listener_step(_LockObservingDev(), state, arb)
    assert observed["held"] is True


def test_arbitrated_dispatcher_start_barriers_on_in_flight_poll():
    # FIX 2: a "start" must wait for any in-flight board poll (holding the device
    # lock) to finish before driving the controller — the transition barrier. If
    # the lock is held, the controller must not run until it is released.
    from uwb_explorer.experiments.arbiter import PortArbiter, ArbitratedDispatcher
    arb = PortArbiter()
    inner = _RecordingInner(arb)
    wrapped = ArbitratedDispatcher(inner, arb)
    reached = threading.Event()

    arb.try_acquire()                 # simulate an in-flight board poll on the port

    def do_start():
        wrapped.dispatch(parse_command("XS1"))
        reached.set()

    t = threading.Thread(target=do_start)
    t.start()
    time.sleep(0.1)
    assert not reached.is_set()       # blocked at the barrier the whole time
    assert inner.cmds == []           # the controller has NOT run yet
    arb.release()                     # the in-flight poll finishes
    t.join(timeout=2.0)
    assert reached.is_set()           # start proceeded once the port was free
    assert len(inner.cmds) == 1       # controller ran only after the barrier


# --- quiesce handshake: the board's listener-stop provably precedes the start --

def test_pause_clears_quiesced_only_when_a_listener_is_running():
    # FIX 2: pause() must only arm the quiesce wait when a board listener is
    # actually up. With NO listener running there is nothing to wait for, so
    # quiesced stays SET and wait_quiesced returns immediately (existing unit
    # tests, which never start a listener, must not block). With a listener
    # running, pause() clears quiesced so the controller's start blocks until
    # the board calls mark_quiesced() after stopping its listener.
    from uwb_explorer.experiments.arbiter import PortArbiter
    arb = PortArbiter()

    # no listener: pause leaves quiesced set -> immediate True
    arb.pause()
    assert arb.wait_quiesced(timeout=0.0) is True
    arb.resume()

    # listener up: pause clears quiesced -> wait times out until mark_quiesced
    arb.set_listener_running(True)
    arb.pause()
    assert arb.wait_quiesced(timeout=0.05) is False   # board hasn't stopped yet
    arb.mark_quiesced()                                # board stopped its listener
    assert arb.wait_quiesced(timeout=0.0) is True      # now the port is free


def test_start_waits_for_quiesce_then_dispatches():
    # FIX 2: with a listener running, a "start" must BLOCK in wait_quiesced until
    # the board thread has stopped its listener and called mark_quiesced. Proof:
    # a background thread marks quiesced only after a delay, and the inner
    # controller must observe that mark already happened when it finally runs.
    from uwb_explorer.experiments.arbiter import PortArbiter, ArbitratedDispatcher
    arb = PortArbiter()
    arb.set_listener_running(True)     # a board listener is up

    marked = threading.Event()
    saw_marked: list[bool] = []

    class _MarkObservingInner:
        def dispatch(self, cmd):
            saw_marked.append(marked.is_set())
            return {"ok": True, "exp": cmd.exp, "action": cmd.action}

    inner = _MarkObservingInner()
    wrapped = ArbitratedDispatcher(inner, arb, start_timeout=2.0)

    def board_quiesces():
        time.sleep(0.15)
        marked.set()
        arb.mark_quiesced()            # board is now off the port

    t = threading.Thread(target=board_quiesces)
    t.start()
    result = wrapped.dispatch(parse_command("XS1"))
    t.join(timeout=2.0)

    assert saw_marked == [True]        # controller ran only AFTER the board quiesced
    assert result == {"ok": True, "exp": "S", "action": "start"}


def test_start_proceeds_best_effort_if_quiesce_times_out():
    # FIX 2 (degraded case): if a listener is up but the board never marks
    # quiesced (wedged/absent), the start must NOT hang forever — after the small
    # timeout it proceeds best-effort so the system stays responsive.
    from uwb_explorer.experiments.arbiter import PortArbiter, ArbitratedDispatcher
    arb = PortArbiter()
    arb.set_listener_running(True)     # listener up, but nobody will quiesce
    inner = _RecordingInner(arb)
    wrapped = ArbitratedDispatcher(inner, arb, start_timeout=0.05)

    result = wrapped.dispatch(parse_command("XS1"))
    assert len(inner.cmds) == 1        # dispatched best-effort despite no quiesce
    assert result == {"ok": True, "exp": "S", "action": "start"}


def test_arbitrated_dispatcher_leaves_arbiter_untouched_on_status():
    # a "status" query neither pauses nor resumes — it must not disturb whatever
    # the arbiter state is, while still delegating to the inner dispatcher.
    from uwb_explorer.experiments.arbiter import PortArbiter, ArbitratedDispatcher
    arb = PortArbiter()
    inner = _RecordingInner(arb)
    wrapped = ArbitratedDispatcher(inner, arb)

    wrapped.dispatch(parse_command("XS?"))
    assert arb.is_active() is False            # untouched
    assert len(inner.cmds) == 1                # but still delegated


# --- pump(): drive a multi-step experiment forward (bug nmr) -----------------
# The scanner/transponder sweep only ever ran combo 0 because nothing called
# controller.step() after start(). The board thread (which owns the port during
# the active window) must PUMP the active controller. ArbitratedDispatcher knows
# which controller a start drove, so it exposes pump(); board_loop calls it.

class _SteppableController:
    """A controller whose sweep needs 3 steps total (start does #0)."""

    def __init__(self):
        self.steps = 0
        self.running = False

    def start(self, args):
        self.running = True
        return {"ok": True}

    def stop(self, args):
        self.running = False
        return {"ok": True}

    def status(self, args):
        return {"running": self.running}

    def step(self):
        self.steps += 1
        return self.steps < 3   # two more steps of work after start


class _SteppableInner:
    """Inner dispatcher exposing controller_for so the wrapper can find step()."""

    def __init__(self, controller):
        self._c = controller
        self.cmds = []

    def dispatch(self, cmd):
        self.cmds.append(cmd)
        return getattr(self._c, cmd.action)(cmd.args)

    def controller_for(self, exp):
        return self._c


def test_pump_advances_the_active_steppable_after_start():
    from uwb_explorer.experiments.arbiter import PortArbiter, ArbitratedDispatcher
    arb = PortArbiter()
    ctrl = _SteppableController()
    wrapped = ArbitratedDispatcher(_SteppableInner(ctrl), arb)

    wrapped.dispatch(parse_command("XS1"))     # start
    assert wrapped.pump() is True              # step 1 (more remains)
    assert wrapped.pump() is True              # step 2
    assert wrapped.pump() is False             # exhausted -> stop pumping
    assert ctrl.steps == 3


def test_pump_resumes_the_arbiter_on_natural_completion():
    # BUG uwb-qorvo-09r: when the pumped sweep EXHAUSTS on its own, the arbiter
    # must be RESUMED so the board's passive listener comes back automatically —
    # without needing an explicit XS0. Previously pump() cleared its target on
    # exhaustion but left the arbiter paused, wedging the listener off the port
    # forever. Using the REAL arbiter: a start pauses it, and the final (empty)
    # pump releases it.
    from uwb_explorer.experiments.arbiter import PortArbiter, ArbitratedDispatcher
    arb = PortArbiter()
    ctrl = _SteppableController()
    wrapped = ArbitratedDispatcher(_SteppableInner(ctrl), arb)

    wrapped.dispatch(parse_command("XS1"))     # start pauses the arbiter
    assert arb.is_active() is True
    assert wrapped.pump() is True              # step 1 (more remains)
    assert wrapped.pump() is True              # step 2
    assert wrapped.pump() is False             # exhausted -> stop pumping
    assert arb.is_active() is False            # listener released automatically


def test_pump_is_a_noop_before_any_start():
    from uwb_explorer.experiments.arbiter import PortArbiter, ArbitratedDispatcher
    arb = PortArbiter()
    ctrl = _SteppableController()
    wrapped = ArbitratedDispatcher(_SteppableInner(ctrl), arb)

    assert wrapped.pump() is False             # nothing active
    assert ctrl.steps == 0


def test_pump_stops_after_the_experiment_is_stopped():
    from uwb_explorer.experiments.arbiter import PortArbiter, ArbitratedDispatcher
    arb = PortArbiter()
    ctrl = _SteppableController()
    wrapped = ArbitratedDispatcher(_SteppableInner(ctrl), arb)

    wrapped.dispatch(parse_command("XS1"))     # start
    wrapped.pump()                             # step 1
    wrapped.dispatch(parse_command("XS0"))     # stop clears the active controller
    before = ctrl.steps
    assert wrapped.pump() is False             # no more stepping after stop
    assert ctrl.steps == before


def test_pump_is_a_noop_when_the_active_controller_has_no_step():
    # placeholder controllers (beacon/fuzzer) have no step(); pumping must be safe
    from uwb_explorer.experiments.arbiter import PortArbiter, ArbitratedDispatcher
    arb = PortArbiter()

    class _NoStep:
        def start(self, args): return {"ok": True}
        def stop(self, args): return {"ok": True}

    class _Inner:
        def __init__(self): self.cmds = []
        def dispatch(self, cmd):
            self.cmds.append(cmd)
            return {"ok": True}
        def controller_for(self, exp): return _NoStep()

    wrapped = ArbitratedDispatcher(_Inner(), arb)
    wrapped.dispatch(parse_command("XB1"))     # beacon placeholder start
    assert wrapped.pump() is False             # nothing steppable, no crash


def test_pump_steps_under_the_device_lock():
    # the pump drives the controller (which touches the serial port), so it MUST
    # hold the device lock — otherwise a listener poll could slip onto the port
    # mid-step. While step() runs, a non-blocking acquire must fail.
    from uwb_explorer.experiments.arbiter import PortArbiter, ArbitratedDispatcher
    arb = PortArbiter()
    observed = {}

    class _LockObservingController:
        running = True
        def start(self, args): return {"ok": True}
        def step(self):
            got = arb.try_acquire(0.0)
            if got:
                arb.release()
            observed["held"] = not got
            return False

    class _Inner:
        def __init__(self): self._c = _LockObservingController()
        def dispatch(self, cmd): return getattr(self._c, cmd.action)(cmd.args)
        def controller_for(self, exp): return self._c

    wrapped = ArbitratedDispatcher(_Inner(), arb)
    wrapped.dispatch(parse_command("XS1"))
    wrapped.pump()
    assert observed["held"] is True


def test_board_loop_accepts_a_pump_param():
    # board_loop is hardware-bound; only assert the seam exists so the board
    # thread can pump the active experiment forward.
    sig = inspect.signature(web.board_loop)
    assert "pump" in sig.parameters
