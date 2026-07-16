"""TDD (RED phase) for the shared SweepController Template-Method base.

bead uwb-qorvo-1hu.20: ScannerController and TransponderController were
near-verbatim copies. This module hosts what they share so a hardware-found
fix lands ONCE and benefits both:

  * ``_parse_csv`` — the channels/pcodes arg parsing.
  * ``sweep_plan``/``SweepStep`` — the cartesian PHY-combo plan.
  * the injected-clock results-folding scaffolding (``now``/``sleep``/``dwell``).
  * the thread-free start/step/stop stepping skeleton.
  * the repeated-poll dwell (bug uwb-qorvo-4hg): the dwell budget is spent as
    several short sub-poll slices, draining after each and stopping early on a
    fresh hit, so a reply that lands part-way through the window is caught.
  * running-clears-on-exhaustion (bug uwb-qorvo-09r): ``step()`` clears
    ``_running`` once the plan is exhausted, and ``status()`` reports
    ``running`` as False from that point on with no explicit ``stop()``.
  * the config-set "ok" flush before the role-specific ranging command.
  * the status skeleton (running/total/step/channels/pcodes + a per-subclass
    results key).

Per-subclass hooks (NOT shared, exercised here via a minimal fake subclass):
  * ``_results_cls`` — the Results type to instantiate.
  * ``_results_key`` — the status dict key the results list is reported under.
  * ``_start_ranging(ch, pc)`` — the role-specific ranging command
    (``start_initf`` for the scanner, ``start_respf`` for the transponder).

The module under test does not exist yet; the failing import IS the RED signal.
GREEN implements ``uwb_explorer/experiments/sweep.py`` to satisfy these tests.
"""

from __future__ import annotations

import json

import pytest

from tests.test_device import UWBCFG_REPLY, make_device, make_scripted

from uwb_explorer.experiments.sweep import (
    DEFAULT_CHANNELS,
    DEFAULT_PCODES,
    SweepController,
    SweepStep,
    sweep_plan,
)


# ---- the sweep plan (shared, identical to the pre-refactor scanner's) ------

def test_sweep_plan_default_length_and_full_product():
    plan = sweep_plan()
    assert len(plan) == 2 * 4 == 8
    combos = [(s.channel, s.pcode) for s in plan]
    assert (5, 9) in combos and (9, 12) in combos
    assert len(set(combos)) == 8


def test_sweep_plan_custom_args_are_the_cartesian_product_in_order():
    plan = sweep_plan(channels=(5, 9), pcodes=(9, 10))
    assert [(s.channel, s.pcode) for s in plan] == [
        (5, 9), (5, 10), (9, 9), (9, 10),
    ]


def test_sweep_step_carries_the_phy_combo():
    step = sweep_plan(channels=(9,), pcodes=(11,))[0]
    assert isinstance(step, SweepStep)
    assert step.channel == 9
    assert step.pcode == 11


def test_default_channels_and_pcodes_are_exposed():
    assert DEFAULT_CHANNELS == (5, 9)
    assert DEFAULT_PCODES == (9, 10, 11, 12)


# ---- a minimal concrete subclass exercising the Template Method hooks -----

class _FakeResults:
    """Minimal results double satisfying the base's duck-typed contract."""

    def __init__(self):
        self._items: list[tuple] = []
        self._hits = 0

    @property
    def hits(self) -> int:
        return self._hits

    def record(self, step, event, timestamp) -> None:
        # count every polled event as a "hit" so the dwell-slicing loop under
        # test can observe early-stop behavior deterministically
        self._hits += 1
        self._items.append((step.channel, step.pcode, timestamp))

    def to_list(self) -> list:
        return list(self._items)


class _FakeSweepController(SweepController):
    """Fires start_initf as its stand-in role-specific ranging command."""

    _results_cls = _FakeResults
    _results_key = "items"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.ranging_calls: list[tuple[int, int]] = []

    def _start_ranging(self, ch: int, pc: int) -> None:
        self.ranging_calls.append((ch, pc))
        self._device.start_initf(CHAN=ch, PCODE=pc)


def _fixed_clock(t=1000.0):
    return lambda: t


def test_subclass_missing_results_hooks_raises_at_construction():
    dev, _ = make_device()

    class _Incomplete(SweepController):
        def _start_ranging(self, ch, pc):
            pass

    with pytest.raises(NotImplementedError):
        _Incomplete(dev)


def test_base_is_abstract_about_start_ranging():
    # SweepController itself must not be directly instantiable — subclasses
    # MUST supply the role-specific ranging command.
    dev, _ = make_device()
    with pytest.raises(TypeError):
        SweepController(dev)  # type: ignore[abstract]


def test_start_drives_the_first_combo_via_the_ranging_hook():
    dev, ser = make_scripted({"uwbcfg": UWBCFG_REPLY})
    ctrl = _FakeSweepController(dev, now=_fixed_clock(), dwell=0)
    ctrl.start({})  # defaults -> first combo is channel 5, pcode 9

    assert ctrl.ranging_calls == [(5, 9)]
    tx = bytes(ser.tx)
    assert b"uwbcfg 5 " in tx
    assert b"initf -CHAN=5 -PCODE=9" in tx


def test_status_reports_the_results_under_the_subclass_key():
    dev, _ = make_scripted({"uwbcfg": UWBCFG_REPLY})
    ctrl = _FakeSweepController(dev, now=_fixed_clock(), dwell=0)
    ctrl.start({})

    st = ctrl.status({})
    assert "items" in st
    assert "devices" not in st
    assert st["total"] == 8
    assert st["step"] >= 1
    assert list(st["channels"]) == list(DEFAULT_CHANNELS)
    assert list(st["pcodes"]) == list(DEFAULT_PCODES)
    json.dumps(st)


def test_blank_args_use_defaults_and_custom_args_are_honored():
    dev, _ = make_scripted({"uwbcfg": UWBCFG_REPLY})
    ctrl = _FakeSweepController(dev, now=_fixed_clock(), dwell=0)

    ctrl.start({})
    assert ctrl.status({})["total"] == 8

    ctrl.start({"channels": "5,9", "pcodes": "9,10"})
    assert ctrl.status({})["total"] == 4


def test_step_drives_the_next_combo_without_threads():
    dev, ser = make_scripted({"uwbcfg": UWBCFG_REPLY})
    ctrl = _FakeSweepController(dev, now=_fixed_clock(), dwell=0)
    ctrl.start({"channels": "5,9", "pcodes": "9"})  # drives (5, 9)

    assert ctrl.step() is True  # drives (9, 9)
    assert ctrl.ranging_calls == [(5, 9), (9, 9)]


def test_stop_sends_stop_and_leaves_the_device_idle():
    dev, ser = make_device()
    ctrl = _FakeSweepController(dev, now=_fixed_clock())
    ctrl.stop({})
    assert b"stop\r\n" in bytes(ser.tx)
    assert dev.mode == "STOP"
    assert ctrl.status({})["running"] is False


# ---- repeated-poll dwell (bug uwb-qorvo-4hg), at the base level -----------

def test_dwell_budget_is_spent_as_several_short_sub_poll_slices():
    dev, _ = make_scripted({"uwbcfg": UWBCFG_REPLY})
    slept: list[float] = []
    ctrl = _FakeSweepController(dev, now=_fixed_clock(),
                                 sleep=lambda s: slept.append(s), dwell=0.75)
    ctrl.start({"channels": "9", "pcodes": "9"})
    # no reply lands (the fake results double only "hits" via record(), and
    # nothing calls record() here because poll_events() drains nothing new),
    # so every slice is used and the total slept equals the configured dwell.
    assert sum(slept) == pytest.approx(0.75)


def test_repeated_polling_catches_a_reply_that_lands_partway_through_the_dwell():
    # a responder that only answers after a couple of sub-poll slices; a lone
    # drain-after-sleep would miss it, but repeated draining across the dwell
    # budget catches it (bug 4hg).
    RANGING_REPLY = (
        b'{"Block":1,"results":[{"Addr":"0x0001","Status":"Ok","D_cm":42}]}\r\n'
    )
    dev, ser = make_scripted({"uwbcfg": UWBCFG_REPLY})  # NOTE: no initf reply
    calls = {"n": 0}

    def late_reply(_secs):
        calls["n"] += 1
        if calls["n"] == 3:
            ser.feed(RANGING_REPLY)

    ctrl = _FakeSweepController(dev, now=_fixed_clock(1000.0),
                                 sleep=late_reply, dwell=0.5)
    ctrl.start({"channels": "9", "pcodes": "9"})

    items = ctrl.status({})["items"]
    assert len(items) == 1  # the fake results double recorded exactly one hit


def test_a_fresh_hit_stops_polling_early_without_burning_the_rest_of_the_dwell():
    dev, ser = make_scripted({"uwbcfg": UWBCFG_REPLY})
    calls = {"n": 0}
    RANGING_REPLY = (
        b'{"Block":1,"results":[{"Addr":"0x0001","Status":"Ok","D_cm":42}]}\r\n'
    )

    def immediate_reply(_secs):
        calls["n"] += 1
        if calls["n"] == 1:
            ser.feed(RANGING_REPLY)

    ctrl = _FakeSweepController(dev, now=_fixed_clock(),
                                 sleep=immediate_reply, dwell=0.5, poll_slices=5)
    ctrl.start({"channels": "9", "pcodes": "9"})

    # only 1 of the 5 slices should have run: the hit on slice 1 stops early
    assert calls["n"] == 1


# ---- running-clears-on-exhaustion (bug uwb-qorvo-09r), at the base level --

def test_running_clears_when_the_sweep_is_exhausted():
    dev, _ = make_scripted({"uwbcfg": UWBCFG_REPLY})
    ctrl = _FakeSweepController(dev, now=_fixed_clock(), dwell=0)
    ctrl.start({"channels": "5,9", "pcodes": "9"})   # two combos, first driven
    assert ctrl.status({})["running"] is True         # combo 2 still pending

    assert ctrl.step() is True    # drive combo 2 (now index == total)
    assert ctrl.step() is False   # exhausted
    assert ctrl.status({})["running"] is False
