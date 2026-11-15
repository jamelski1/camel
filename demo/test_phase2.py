"""Phase 2 verification and evidence runs for the CAMEL Playground.

Runs fully offline against the stub model and scripted transcripts; no API key
is needed. It checks the three Phase 2 enhancements and prints the numbers the
Phase 2 report's evidence table is built from:

  Enhancement 1 (persistent session history): start a session, run it, reopen
  it from the store in a fresh Playground, and count how many of the saved
  fields come back exactly, plus the restore time.
  Enhancement 2 (progress and termination controls): a scripted transcript in
  which the assistant repeats itself, and the turn at which the monitor flags
  the repetition and the stall; the configurable turn limit and pause/resume.
  Enhancement 3 (model configuration and error recovery): preflight catching
  bad configurations before a run starts, and retry recovering from simulated
  transient provider failures while failing fast on a rejected key.

Run:  python demo/test_phase2.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from model_config import call_with_retry, classify_error, preflight  # noqa: E402
from progress_monitor import ProgressMonitor  # noqa: E402
from session_store import RESTORED_FIELDS, SessionStore  # noqa: E402

FAILURES = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        FAILURES.append(label)


# --------------------------------------------------------------------------
print("Enhancement 2a: scripted stall, repetition, drift, completion")
mon = ProgressMonitor(task_text="Write a haiku about autumn leaves falling")
A, U = "Poet", "Editor"
haiku = "Autumn leaves drift down softly over the quiet garden path"
mon.observe(1, [(U, "Write a haiku about autumn leaves"),
                (A, haiku)], A, U)
rep = mon.observe(2, [(U, "Try another version please"), (A, haiku)], A, U)
check("repetition flagged at turn 2", any(f.kind == "repetition" for f in rep),
      "; ".join(f.detail for f in rep))
mon.observe(3, [(U, "Try another version please"), (A, haiku)], A, U)
stall = mon.observe(4, [(U, "Try another version please"), (A, haiku)], A, U)
check("stall flagged at turn 4", any(f.kind == "stall" for f in stall),
      "; ".join(f.detail for f in stall))
first_stall_turn = min(f.turn for f in mon.flags if f.kind == "stall")

drift = ProgressMonitor(task_text="Plan a weekend trip to Montreal on a student budget")
drift.observe(1, [(U, "Plan the Montreal weekend trip"),
                  (A, "We can budget the train and a hostel in Montreal")], A, U)
drift.observe(2, [(U, "What about food costs for the weekend"),
                  (A, "Street food keeps the Montreal budget low")], A, U)
d3 = drift.observe(3, [(U, "Explain quantum entanglement instead"),
                       (A, "Quantum particles share correlated states instantly")], A, U)
check("drift flagged at turn 3", any(f.kind == "drift" for f in d3),
      "; ".join(f.detail for f in d3))
comp = drift.observe(4, [(U, "Great, that completes the task, all done"),
                         (A, "Quantum tunnelling affects semiconductor design")], A, U)
check("possible completion flagged", any(f.kind == "possible_completion" for f in comp))

# --------------------------------------------------------------------------
print("Enhancement 3a: preflight catches bad configurations before a run")
from role_playing_demo import PLATFORMS  # noqa: E402

cases = [
    ("offline stub", preflight(PLATFORMS, None, None, stub=True), True),
    ("openai with no key set", preflight(PLATFORMS, "openai", None, env={}), False),
    ("openai with a key set",
     preflight(PLATFORMS, "openai", None, env={"OPENAI_API_KEY": "sk-test"}), True),
    ("ollama needs no key", preflight(PLATFORMS, "ollama", None, env={}), True),
    ("unknown provider, no model",
     preflight(PLATFORMS, "nosuchprovider", None, env={}), False),
]
caught = passed = 0
for label, result, expected_ok in cases:
    check(f"preflight: {label}", result.ok == expected_ok,
          "ok" if result.ok else "; ".join(
              f"{c.name}: {c.detail}" for c in result.checks if not c.ok))
    if not expected_ok and not result.ok:
        caught += 1
    if expected_ok and result.ok:
        passed += 1
bad_total = sum(1 for _, _, ok in cases if not ok)
good_total = sum(1 for _, _, ok in cases if ok)


def _boom(platform, model):
    raise RuntimeError("429 Too Many Requests")


create_fail = preflight(PLATFORMS, "openai", None,
                        env={"OPENAI_API_KEY": "sk-test"}, create=_boom)
check("preflight: model construction failure is reported, not raised",
      not create_fail.ok and classify_error(RuntimeError("429")) == "rate_limit")

print("Enhancement 3b: retry recovers transient failures, fails fast on auth")
retry_events = []


def flaky(failures: int, message: str):
    state = {"calls": 0}

    def fn():
        state["calls"] += 1
        if state["calls"] <= failures:
            raise RuntimeError(message)
        return "recovered"

    return fn, state


recovered = 0
scenarios = [(1, "429 Too Many Requests"), (2, "request timed out"),
             (1, "connection reset by peer")]
for fails, message in scenarios:
    fn, _ = flaky(fails, message)
    out = call_with_retry(fn, sleep=lambda s: None,
                          on_retry=lambda a, c, e: retry_events.append(c))
    if out == "recovered":
        recovered += 1
check("simulated transient failures recovered by retry",
      recovered == len(scenarios), f"{recovered}/{len(scenarios)}")

auth_fn, auth_state = flaky(5, "401 invalid api key")
try:
    call_with_retry(auth_fn, sleep=lambda s: None)
    check("rejected key fails fast", False, "no exception raised")
except RuntimeError:
    check("rejected key fails fast", auth_state["calls"] == 1,
          f"attempts made: {auth_state['calls']}")

# --------------------------------------------------------------------------
print("Enhancements 1+2b: live stub session, save, reopen, limits, pause")
from playground import Playground  # noqa: E402

db = Path(tempfile.mkdtemp()) / "sessions.db"
pg = Playground(db_path=str(db))
pg.start("Write a small trading bot in Python", "Python Programmer", "Stock Trader",
         None, None, True, True, max_turns=12)
check("session saved at start", pg.session_id is not None, f"id {pg.session_id}")
pg.run_turns(2)
pg.guide("Focus on risk checks next")
pg.instruct("Add a function that computes a moving average")
snapshot = {
    "task_original": pg.task_original, "specified_task": pg.specified_task,
    "assistant_role": pg.assistant_role, "user_role": pg.user_role,
    "platform": pg.platform_name, "model_name": pg.model_label,
    "mode": pg.mode, "concise": pg.concise, "turn": pg.turn,
    "status": "in_progress",
}
transcript_before = list(pg.transcript)
turn_before = pg.turn

store = SessionStore(db)
row = store.get_session(pg.session_id)
store.close()
check("guidance note persisted", bool(row["guidance"]) and
      row["guidance"][0]["text"] == "Focus on risk checks next")

t0 = time.perf_counter()
pg2 = Playground.reopen(pg.session_id, str(db))
restore_seconds = time.perf_counter() - t0
restored = {
    "task_original": pg2.task_original, "specified_task": pg2.specified_task,
    "assistant_role": pg2.assistant_role, "user_role": pg2.user_role,
    "platform": pg2.platform_name, "model_name": pg2.model_label,
    "mode": pg2.mode, "concise": pg2.concise, "turn": pg2.turn,
    "status": "done" if pg2.done else "in_progress",
}
matched = sum(1 for f in RESTORED_FIELDS if restored[f] == snapshot[f])
check(f"reopened fields match ({matched}/{len(RESTORED_FIELDS)})",
      matched == len(RESTORED_FIELDS),
      "; ".join(f"{f}: {restored[f]!r} != {snapshot[f]!r}"
                for f in RESTORED_FIELDS if restored[f] != snapshot[f]))
check("transcript restored exactly", pg2.transcript == transcript_before,
      f"{len(pg2.transcript)} entries")
pg2.run_turns(1)
check("reopened session continues", pg2.turn == turn_before + 1,
      f"turn {turn_before} -> {pg2.turn}")

pg3 = Playground(db_path=str(db))
pg3.start("Plan a weekend in Montreal", "Travel Planner", "Student",
          None, None, True, False, max_turns=2)
pg3.run_turns(5)
check("turn limit stops the run", pg3.turn == 2 and
      pg3.terminated.startswith("Turn limit"), f"turn {pg3.turn}, {pg3.terminated!r}")
pg3.set_paused(True)
check("paused run makes no progress", pg3.run_turns(1) == [] and pg3.turn == 2)
pg3.set_paused(False)

# --------------------------------------------------------------------------
print()
print("EVIDENCE SUMMARY")
print(f"  E1 fields restored on reopen: {matched}/{len(RESTORED_FIELDS)} "
      f"(baseline: 0/{len(RESTORED_FIELDS)}; Phase 1 kept sessions in memory only)")
print(f"  E1 restore time: {restore_seconds:.3f} s for a {len(transcript_before)}-entry transcript")
print(f"  E2 scripted stall: repetition flagged at turn 2, stall flagged at turn "
      f"{first_stall_turn} (baseline: never detected)")
print(f"  E2 turn limit: run stopped at 2 of 2 turns; pause held the run at turn 2")
print(f"  E3 preflight: {caught}/{bad_total} bad configurations caught before start; "
      f"{passed}/{good_total} good configurations passed")
print(f"  E3 retry: {recovered}/{len(scenarios)} simulated transient failures recovered; "
      "a rejected key failed on the first attempt with no retries")
print()
if FAILURES:
    print(f"RESULT: {len(FAILURES)} check(s) failed: {FAILURES}")
    sys.exit(1)
print("RESULT: all checks passed")
