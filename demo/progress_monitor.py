"""Progress and termination signals for the CAMEL Playground (Phase 2, Enhancement 2).

Phase 1's Narrator was a language model asked, in prose, whether the agents
were repeating themselves, drifting, or stalling. That costs an extra model
call, and its judgement cannot be tested. This module computes the same signals
locally and deterministically from the transcript, with no model call:

  * repetition: the newest message from an agent is nearly identical to one of
    that agent's recent messages (token-set Jaccard similarity).
  * stall: several agent turns in a row add almost no new words to the
    conversation (novelty below a floor), so the run is going in circles.
  * drift: the recent exchanges barely mention the task's own keywords.
  * possible completion: the instructing side uses explicit completion
    language ("task complete", "all done", ...) without the formal done token.

The Playground shows each flag in the chat as a Narrator progress check and
counts them in the status strip. The configurable turn limit and the pause and
resume controls live in playground.py; the detection lives here so it can be
unit tested on scripted transcripts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "is", "are", "be", "we", "you", "i", "it", "this", "that", "as", "at",
    "by", "from", "our", "your", "their", "will", "would", "can", "could",
    "should", "about", "into", "over", "after", "before", "between",
}

COMPLETION_PHRASES = (
    "task complete", "task is complete", "all done", "we are done",
    "that completes", "successfully completed", "goal achieved",
)


def tokens(text: str) -> set[str]:
    """Lowercased word tokens with markdown and punctuation stripped."""
    return set(re.findall(r"[a-z0-9']+", (text or "").lower()))


def keywords(text: str) -> set[str]:
    """The content words of a text: its tokens minus stopwords and short words."""
    return {t for t in tokens(text) if t not in STOPWORDS and len(t) > 2}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class ProgressFlag:
    kind: str        # repetition | stall | drift | possible_completion
    turn: int
    detail: str

    def line(self) -> str:
        return f"Progress check: {self.detail}"


@dataclass
class ProgressMonitor:
    """Watches a transcript grow and flags repetition, stalls, and drift."""

    task_text: str = ""
    repeat_threshold: float = 0.8     # Jaccard at or above this = a repeat
    novelty_floor: float = 0.15       # below this share of new words = no progress
    stall_window: int = 2             # consecutive low-novelty turns = a stall
    drift_floor: float = 0.05         # task-keyword share below this = drift

    _history: dict = field(default_factory=dict)   # speaker -> list of token sets
    _seen: set = field(default_factory=set)        # every token seen so far
    _low_novelty_streak: int = 0
    _stall_reported: bool = False
    _last_drift_turn: int = -10
    flags: list = field(default_factory=list)

    def __post_init__(self) -> None:
        self._task_keywords = keywords(self.task_text)

    # ----- one call per completed turn --------------------------------------
    def observe(self, turn: int, entries: list, assistant_role: str,
                user_role: str) -> list:
        """Check the newest turn's agent messages; return any new flags."""
        new_flags: list[ProgressFlag] = []
        agent_entries = [
            (who, text) for who, text in entries if who in (assistant_role, user_role)
        ]
        for who, text in agent_entries:
            toks = tokens(text)
            prior = self._history.setdefault(who, [])
            for old in prior[-3:]:
                sim = jaccard(toks, old)
                if sim >= self.repeat_threshold:
                    new_flags.append(ProgressFlag(
                        "repetition", turn,
                        f"{who} is repeating an earlier message "
                        f"({sim:.0%} similar) at turn {turn}."))
                    break
            prior.append(toks)
            if who == user_role:
                low = text.lower()
                if any(p in low for p in COMPLETION_PHRASES):
                    new_flags.append(ProgressFlag(
                        "possible_completion", turn,
                        f"{user_role} used completion language at turn {turn} "
                        "without the formal done signal."))
        if agent_entries:
            new_flags += self._novelty_check(turn, agent_entries)
            new_flags += self._drift_check(turn, agent_entries)
        self.flags.extend(new_flags)
        return new_flags

    def _novelty_check(self, turn: int, agent_entries: list) -> list:
        combined: set = set()
        for _, text in agent_entries:
            combined |= tokens(text)
        if not combined:
            return []
        novelty = len(combined - self._seen) / len(combined)
        self._seen |= combined
        if novelty < self.novelty_floor:
            self._low_novelty_streak += 1
        else:
            self._low_novelty_streak = 0
            self._stall_reported = False
        if self._low_novelty_streak >= self.stall_window and not self._stall_reported:
            self._stall_reported = True
            return [ProgressFlag(
                "stall", turn,
                f"The last {self._low_novelty_streak} turns added almost no new "
                f"content. The run looks stalled at turn {turn}.")]
        return []

    def _drift_check(self, turn: int, agent_entries: list) -> list:
        if turn < 3 or not self._task_keywords:
            return []
        if turn - self._last_drift_turn < 2:
            return []
        recent: set = set()
        for _, text in agent_entries:
            recent |= tokens(text)
        share = len(recent & self._task_keywords) / len(self._task_keywords)
        if share < self.drift_floor:
            self._last_drift_turn = turn
            return [ProgressFlag(
                "drift", turn,
                f"Recent exchanges barely mention the task itself "
                f"({share:.0%} of its key words) at turn {turn}. "
                "The agents may be drifting from the goal.")]
        return []

    # ----- reporting -----------------------------------------------------------
    def counts(self) -> dict:
        out: dict = {}
        for flag in self.flags:
            out[flag.kind] = out.get(flag.kind, 0) + 1
        return out

    def summary_line(self) -> str:
        counts = self.counts()
        if not counts:
            return "Progress flags: none so far."
        parts = ", ".join(f"{kind} x{n}" for kind, n in sorted(counts.items()))
        return f"Progress flags: {parts}. Latest: {self.flags[-1].detail}"
