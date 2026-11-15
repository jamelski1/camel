"""Phase 2 benchmark suite for the CAMEL Playground (SENG 691).

Companion to test_phase2.py. Where that file checks that each enhancement
works once, this suite measures how well, on data whose right answers are
known in advance. Everything runs offline and deterministically; no API key
is needed.

  Benchmark A (progress monitor accuracy): 15 scripted transcripts with
  hand-labelled ground truth (which failure, if any, each contains, and at
  which turn). The monitor is scored per flag kind with precision, recall
  and F1. Construction diagnostics (repeat-pair similarity, per-turn
  novelty, per-turn task-keyword share) are printed alongside, computed
  independently of the monitor's flag logic, so a case can be checked to
  really contain the phenomenon it is labelled with.

  Benchmark B (persistence at scale): 50 sessions of 40 messages each are
  written, the store is reopened cold, and every field, message and
  guidance note is compared exactly, with restore times measured.

  Benchmark C (error handling): a labelled set of provider error strings
  scored against classify_error, and call_with_retry driven through every
  category to tabulate attempts and backoff for transient and permanent
  failures.

Run:  python demo/benchmark_phase2.py
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from model_config import call_with_retry, classify_error  # noqa: E402
from progress_monitor import ProgressMonitor, jaccard, keywords, tokens  # noqa: E402
from session_store import RESTORED_FIELDS, SessionStore  # noqa: E402

A, U = "Helper", "Requester"   # assistant role, user role

# ---------------------------------------------------------------------------
# Benchmark A: monitor accuracy on labelled transcripts.
# Each case: (name, task, [(user_text, assistant_text), ...] per turn,
#             {flag_kind: first_expected_turn})
# ---------------------------------------------------------------------------
CASES = [
    ("R1 exact repeat by the assistant",
     "Write a haiku about autumn leaves falling",
     [("Write a haiku about autumn leaves",
       "Autumn leaves drift down softly over the quiet garden path"),
      ("Try another version please",
       "Autumn leaves drift down softly over the quiet garden path"),
      ("Now make the haiku about rain on rooftops",
       "Rain taps the rooftops while grey clouds gather over sleeping streets"),
      ("Add thunder to the autumn scene",
       "Thunder rolls above the autumn hills as leaves scatter in the wind")],
     {"repetition": 2}),

    ("R2 near repeat with one word changed",
     "Summarize the committee budget decision for the library renovation",
     [("Summarize the committee decision please",
       "The committee approved the budget for the library renovation project this spring"),
      ("What did the committee say about staffing",
       "Members debated hiring two clerks and a part time archivist for the branch"),
      ("Repeat the budget summary once more",
       "The committee approved the budget for the library renovation project this year"),
      ("And what happens next with the renovation",
       "Work begins in June once the permits clear the council office")],
     {"repetition": 3}),

    ("R3 the user repeats their instruction exactly",
     "Plan a birthday dinner menu for twelve guests",
     [("Plan a birthday dinner menu for twelve guests",
       "Start with a tomato soup, then roast chicken with herbs"),
      ("What about a vegetarian option",
       "Add a mushroom risotto and a green salad with walnuts"),
      ("Plan a birthday dinner menu for twelve guests",
       "For dessert serve lemon tart with berries and cream"),
      ("Anything to drink with the dinner",
       "Offer sparkling water, lemonade and one red wine")],
     {"repetition": 3}),

    ("S1 stall by recombining earlier clauses (bookshelf)",
     "Describe how to assemble a wooden bookshelf",
     [("Describe how to assemble the wooden bookshelf",
       "First lay out the side panels, the shelves and the back board, then check "
       "that every pre drilled hole lines up with its matching peg"),
      ("How are the panels joined together",
       "Fit the cam locks into the panels and tighten them with a screwdriver "
       "until each joint feels firm and the frame stands square"),
      ("What holds the shelves in place",
       "Slide each shelf onto its pegs, add the support brackets under the middle "
       "shelf and stand the bookshelf against the wall to check it is level"),
      ("The bookshelf panels use cam locks and pegs",
       "Tighten the cam locks with a screwdriver until each joint feels firm, "
       "then slide each shelf onto its pegs"),
      ("And the bookshelf stands against the wall",
       "Check that every pre drilled hole lines up with its matching peg and "
       "that the frame stands square")],
     {"stall": 5}),

    ("S2 stall by recombining earlier clauses (photosynthesis)",
     "Explain photosynthesis for a school poster",
     [("Explain photosynthesis for the school poster",
       "Green leaves absorb sunlight and convert it into energy while roots "
       "draw water and minerals from the soil below"),
      ("What does the plant make from sunlight",
       "Chlorophyll inside each leaf captures light so the plant can build "
       "glucose from carbon dioxide and release oxygen"),
      ("Which gases move during photosynthesis",
       "The stems carry water upward to the leaves and the whole plant grows "
       "stronger as it stores energy in new tissue"),
      ("In photosynthesis the leaves absorb sunlight and the roots draw water",
       "Roots draw water and minerals from the soil while chlorophyll inside "
       "each leaf captures light"),
      ("And in photosynthesis the plant can build glucose and release oxygen",
       "The stems carry water upward while green leaves absorb sunlight and "
       "convert it into energy")],
     {"stall": 5}),

    ("D1 drift to a new topic at turn 3",
     "Plan a weekend trip to Montreal on a student budget",
     [("Plan the Montreal weekend trip",
       "We can budget the train and a hostel in Montreal"),
      ("What about food costs for the weekend",
       "Street food keeps the Montreal budget low"),
      ("Explain quantum entanglement instead",
       "Quantum particles share correlated states instantly"),
      ("And how do black holes form",
       "Gravity collapses a massive star into a singularity")],
     {"drift": 3}),

    ("D2 drift late, at turn 5",
     "Write a product description for a waterproof hiking backpack",
     [("Write a product description for the backpack",
       "This waterproof backpack keeps gear dry on any hiking trail"),
      ("Mention the straps and zips",
       "Padded straps and sealed zips make the hiking pack comfortable"),
      ("What sizes does the backpack come in",
       "The waterproof backpack comes in twenty and thirty litre sizes"),
      ("Say something about hiking in the rain",
       "Rain rolls off the coated fabric while the pack protects your kit"),
      ("Now tell me how sourdough starter works",
       "Wild yeast ferments flour and water into a living starter")],
     {"drift": 5}),

    ("C1 completion language at turn 2",
     "Book a table for two on Friday night",
     [("Book a table for two on Friday night",
       "The bistro has a table at seven or at nine"),
      ("Seven works. That completes the booking, all done",
       "Confirmed for Friday at seven under your name")],
     {"possible_completion": 2}),

    ("C2 completion language at turn 4, on task",
     "Draft an invitation to the neighborhood cleanup day",
     [("Draft an invitation to the neighborhood cleanup day",
       "Join us Saturday morning to clean the park and plant flowers"),
      ("Add the meeting point and time",
       "Meet at the community gate at nine; gloves and bags provided"),
      ("Mention refreshments in the invitation",
       "We will serve lemonade and sandwiches after the cleanup"),
      ("The invitation looks complete, task is complete",
       "I will format the invitation for printing this afternoon")],
     {"possible_completion": 4}),

    ("M1 repeat and drift together",
     "Write a jingle for a coffee brand",
     [("Write a jingle for our coffee brand",
       "Wake up smiling, sip the morning, coffee makes the day begin"),
      ("Actually, explain stock options first",
       "A stock option gives the right to buy shares later at a fixed price"),
      ("Say that options part again",
       "A stock option gives the right to buy shares later at a fixed price"),
      ("And what is a dividend",
       "A dividend pays shareholders part of a company profit")],
     {"repetition": 3, "drift": 3}),

    ("H1 healthy garden planning, six turns",
     "Plan a vegetable garden for a small backyard",
     [("Plan a vegetable garden for my small backyard",
       "Start by measuring the sunny beds and testing the soil"),
      ("Which vegetables suit a shaded corner",
       "Leafy greens like spinach and chard tolerate partial shade"),
      ("How often should the garden be watered",
       "Water deeply twice a week and mulch around the seedlings"),
      ("What keeps pests away from the vegetable beds",
       "Marigolds and netting deter aphids without sprays"),
      ("When do we harvest the backyard tomatoes",
       "Pick tomatoes when they turn fully red and feel firm"),
      ("Any tips for winter planning for the garden",
       "Order seeds early and sketch next season's garden layout")],
     {}),

    ("H2 healthy exam preparation, five turns",
     "Prepare a study schedule for a biology exam",
     [("Prepare a study schedule for my biology exam",
       "List the chapters and rank them by how well you know them"),
      ("I struggle with genetics the most",
       "Put genetics first each morning when your focus is freshest"),
      ("How long should each schedule block be",
       "Use fifty minute blocks with ten minute breaks between them"),
      ("Should I take practice tests before the exam",
       "Yes, timed practice under exam conditions exposes weak topics"),
      ("What about the night before the biology test",
       "Review your summary sheets lightly and sleep early")],
     {}),

    ("H3 near miss paraphrase, similar but under the repeat threshold",
     "Describe the water cycle for young students",
     [("Describe the water cycle simply",
       "Water evaporates from lakes and oceans, rises into the sky, cools, "
       "and falls back as rain over the land"),
      ("What happens in the clouds",
       "Cloud droplets gather until they grow heavy enough to fall"),
      ("Say the cycle part again differently",
       "Water evaporates from lakes and oceans, rises into the sky, cools, "
       "comes down as snow over the hills"),
      ("How does the water cycle help farmers",
       "Rainfall timing guides planting, irrigation and harvest planning")],
     {}),

    ("H4 healthy bake sale planning, six turns",
     "Organize a charity bake sale at the office",
     [("Organize a charity bake sale at the office",
       "Pick a Friday and reserve the kitchen counter for displays"),
      ("Who should bake what",
       "Ask volunteers to sign up for cakes, muffins or savory pies"),
      ("How do we price the bake sale items",
       "Price slices at three dollars and bundles at eight"),
      ("What about advertising the sale",
       "Post flyers near the elevators and send one group email"),
      ("Where does the charity money go",
       "Donations go to the food bank; keep a public tally sheet"),
      ("Anything for the office cleanup afterwards",
       "Box leftovers for the shelter and wipe down the counters")],
     {}),
]

KINDS = ("repetition", "stall", "drift", "possible_completion")


def construction_notes(task: str, turns: list) -> str:
    """Independent checks that a case contains what its label claims."""
    task_kw = keywords(task)
    seen: set = set()
    notes = []
    history: dict = {}
    for i, (u_text, a_text) in enumerate(turns, start=1):
        for who, text in ((U, u_text), (A, a_text)):
            toks = tokens(text)
            sims = [jaccard(toks, old) for old in history.get(who, [])[-3:]]
            if sims and max(sims) >= 0.5:
                notes.append(f"t{i} {who} max-sim {max(sims):.2f}")
            history.setdefault(who, []).append(toks)
        combined = tokens(u_text) | tokens(a_text)
        novelty = len(combined - seen) / len(combined) if combined else 0.0
        seen |= combined
        if novelty < 0.3:
            notes.append(f"t{i} novelty {novelty:.2f}")
        if i >= 3 and task_kw:
            share = len(combined & task_kw) / len(task_kw)
            if share < 0.3:
                notes.append(f"t{i} keyword-share {share:.2f}")
    return "; ".join(notes) if notes else "nothing unusual"


def run_monitor_benchmark() -> dict:
    print("=" * 72)
    print("BENCHMARK A: progress monitor accuracy on labelled transcripts")
    print("=" * 72)
    score = {k: {"tp": 0, "fp": 0, "fn": 0} for k in KINDS}
    timing = []
    for name, task, turns, expected in CASES:
        mon = ProgressMonitor(task_text=task)
        for i, (u_text, a_text) in enumerate(turns, start=1):
            mon.observe(i, [(U, u_text), (A, a_text)], A, U)
        got = {}
        for f in mon.flags:
            got.setdefault(f.kind, f.turn)
        for kind in KINDS:
            if kind in expected and kind in got:
                score[kind]["tp"] += 1
                timing.append((name, kind, expected[kind], got[kind]))
            elif kind in expected:
                score[kind]["fn"] += 1
            elif kind in got:
                score[kind]["fp"] += 1
        verdict = "ok" if got == expected else f"EXPECTED {expected} GOT {got}"
        print(f"  {name}: {verdict}")
        print(f"      construction: {construction_notes(task, turns)}")
    print()
    print(f"  {'kind':<22}{'TP':>4}{'FP':>4}{'FN':>4}{'precision':>11}{'recall':>8}{'F1':>7}")
    summary = {}
    for kind in KINDS:
        s = score[kind]
        precision = s["tp"] / (s["tp"] + s["fp"]) if s["tp"] + s["fp"] else 1.0
        recall = s["tp"] / (s["tp"] + s["fn"]) if s["tp"] + s["fn"] else 1.0
        f1 = (2 * precision * recall / (precision + recall)
              if precision + recall else 0.0)
        summary[kind] = (precision, recall, f1)
        print(f"  {kind:<22}{s['tp']:>4}{s['fp']:>4}{s['fn']:>4}"
              f"{precision:>11.2f}{recall:>8.2f}{f1:>7.2f}")
    late = [(n, k, e, g) for n, k, e, g in timing if g != e]
    print(f"  detection timing: {len(timing) - len(late)}/{len(timing)} flags "
          f"landed on the labelled turn"
          + (f"; off-turn: {late}" if late else ""))
    return summary


# ---------------------------------------------------------------------------
# Benchmark B: persistence at scale.
# ---------------------------------------------------------------------------
def run_persistence_benchmark(n_sessions: int = 50, n_turns: int = 20) -> dict:
    print()
    print("=" * 72)
    print(f"BENCHMARK B: persistence at scale "
          f"({n_sessions} sessions x {n_turns} turns)")
    print("=" * 72)
    db_path = Path(tempfile.mkdtemp()) / "stress.db"
    store = SessionStore(db_path)
    expected = {}
    for i in range(n_sessions):
        fields = {
            "task_original": f"Stress task {i}: plan step {i} of the project",
            "specified_task": f"Specified stress task {i} with detail",
            "assistant_role": f"Assistant {i % 5}",
            "user_role": f"Requester {i % 3}",
            "platform": "openai" if i % 2 else "openrouter",
            "model_name": f"model-{i % 4}",
            "mode": "guided" if i % 2 else "auto",
            "concise": bool(i % 2),
            "turn": n_turns,
            "status": "done" if i % 4 == 0 else "in_progress",
        }
        sid = store.create_session(
            task_original=fields["task_original"],
            specified_task=fields["specified_task"],
            assistant_role=fields["assistant_role"],
            user_role=fields["user_role"],
            platform=fields["platform"],
            model_name=fields["model_name"],
            mode=fields["mode"],
            concise=fields["concise"],
        )
        messages = []
        for t in range(1, n_turns + 1):
            for speaker, text in (
                (fields["user_role"], f"Turn {t} instruction for session {i}: "
                                      f"work on item {t} with care"),
                (fields["assistant_role"], f"Turn {t} result for session {i}: "
                                           f"item {t} drafted and checked"),
            ):
                store.append_message(sid, speaker, text)
                messages.append((speaker, text))
        guidance = [f"Guidance for session {i}: keep it short",
                    f"Guidance for session {i}: check the totals"]
        store.add_guidance(sid, 3, guidance[0])
        store.add_guidance(sid, 7, guidance[1])
        store.update_state(sid, turn=fields["turn"], status=fields["status"])
        expected[sid] = (fields, messages, guidance)
    store.close()
    size_kb = db_path.stat().st_size / 1024

    cold = SessionStore(db_path)   # a fresh process-side view of the same file
    fields_ok = fields_total = 0
    messages_ok = messages_total = 0
    guidance_ok = 0
    sessions_exact = 0
    times = []
    for sid, (fields, messages, guidance) in expected.items():
        t0 = time.perf_counter()
        row = cold.get_session(sid)
        times.append(time.perf_counter() - t0)
        good = True
        for f in RESTORED_FIELDS:
            fields_total += 1
            if row[f] == fields[f]:
                fields_ok += 1
            else:
                good = False
        if row["messages"] == messages:
            messages_ok += len(messages)
        else:
            good = False
        messages_total += len(messages)
        if [g["text"] for g in row["guidance"]] == guidance:
            guidance_ok += 1
        else:
            good = False
        if good:
            sessions_exact += 1
    listed = cold.list_sessions(limit=100)
    cold.close()
    mean_ms = 1000 * sum(times) / len(times)
    max_ms = 1000 * max(times)
    print(f"  sessions restored exactly: {sessions_exact}/{n_sessions}")
    print(f"  setup fields restored exactly: {fields_ok}/{fields_total}")
    print(f"  messages restored exactly: {messages_ok}/{messages_total}")
    print(f"  sessions with both guidance notes: {guidance_ok}/{n_sessions}")
    print(f"  restore time per session: mean {mean_ms:.1f} ms, max {max_ms:.1f} ms")
    print(f"  database size for {n_sessions} sessions: {size_kb:.0f} KB")
    print(f"  list_sessions returned {len(listed)} rows")
    return {"sessions_exact": sessions_exact, "n": n_sessions,
            "messages_ok": messages_ok, "messages_total": messages_total,
            "mean_ms": mean_ms, "max_ms": max_ms, "size_kb": size_kb}


# ---------------------------------------------------------------------------
# Benchmark C: error classification and retry behaviour.
# ---------------------------------------------------------------------------
CLASSIFY_CASES = [
    ("429 Too Many Requests", "rate_limit"),
    ("Rate limit exceeded for this key", "rate_limit"),
    ("Quota exceeded, too many requests today", "rate_limit"),
    ("401 Unauthorized", "auth"),
    ("Incorrect API key provided", "auth"),
    ("403 Forbidden", "auth"),
    ("404 Not Found: the model does not exist", "model_not_found"),
    ("No endpoints found for that model", "model_not_found"),
    ("Request timeout after 60 seconds", "timeout"),
    ("The connection was reset by the peer", "connection"),
    ("Failed to connect to host", "connection"),
    ("Internal server error 500", "unknown"),
]

RETRY_ERRORS = [
    ("rate_limit", "429 Too Many Requests"),
    ("timeout", "The request timed out after 30 seconds"),
    ("connection", "Connection reset by peer"),
    ("auth", "401 Unauthorized: invalid api key"),
    ("model_not_found", "404: model not found"),
    ("unknown", "Unexpected widget failure in the stream"),
]


def _failing(message: str, failures: int, counter: dict):
    def fn():
        counter["calls"] += 1
        if counter["calls"] <= failures:
            raise RuntimeError(message)
        return "recovered"
    return fn


def run_retry_benchmark() -> dict:
    print()
    print("=" * 72)
    print("BENCHMARK C: error classification and retry behaviour")
    print("=" * 72)
    right = 0
    for text, want in CLASSIFY_CASES:
        got = classify_error(RuntimeError(text))
        right += got == want
        if got != want:
            print(f"  MISCLASSIFIED: {text!r} -> {got}, wanted {want}")
    print(f"  classification accuracy: {right}/{len(CLASSIFY_CASES)}")
    print()
    print(f"  {'category':<18}{'fails twice, then ok':<36}{'always fails':<14}{'backoff (s)'}")
    rows = {}
    for category, message in RETRY_ERRORS:
        counter = {"calls": 0}
        sleeps: list = []
        try:
            out = call_with_retry(_failing(message, 2, counter), sleep=sleeps.append)
            recover_text = (f"recovered in {counter['calls']}"
                            if out == "recovered" else "NOT recovered")
        except RuntimeError:
            recover_text = f"raised on attempt {counter['calls']} (permanent)"
        counter2 = {"calls": 0}
        sleeps2: list = []
        try:
            call_with_retry(_failing(message, 99, counter2), sleep=sleeps2.append)
            attempts_fail = counter2["calls"]
        except RuntimeError:
            attempts_fail = counter2["calls"]
        rows[category] = (recover_text, attempts_fail, sleeps2)
        print(f"  {category:<18}"
              f"{recover_text:<36}"
              f"{attempts_fail:<14}{sleeps2}")
    return {"classify_right": right, "classify_total": len(CLASSIFY_CASES),
            "rows": rows}


if __name__ == "__main__":
    run_monitor_benchmark()
    run_persistence_benchmark()
    run_retry_benchmark()
    print()
    print("DONE")
