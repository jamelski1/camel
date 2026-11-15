"""Persistent session history for the CAMEL Playground (SENG 691 Phase 2, Enhancement 1).

Phase 1 kept a session only in Gradio memory: restarting the app lost the task,
the roles, the transcript, and the model settings. This module stores them in a
small SQLite database (Python standard library only, no new dependency), using
the schema the team proposed in the Phase 1 ER diagram: a session row (the
AGENT_SESSION entity, with the task and model configuration fields it points
to), one row per transcript message (MESSAGE), and one row per human guidance
note (GUIDANCE).

The Playground writes through to this store as a session runs, so what is on
disk is always the current session, and any saved session can be reopened after
a restart with its task, roles, transcript, guidance, model configuration, turn
count, and completion status intact.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    task_original  TEXT NOT NULL,
    specified_task TEXT NOT NULL DEFAULT '',
    assistant_role TEXT NOT NULL,
    user_role      TEXT NOT NULL,
    platform       TEXT NOT NULL DEFAULT '',
    model_name     TEXT NOT NULL DEFAULT '',
    mode           TEXT NOT NULL DEFAULT '',
    concise        INTEGER NOT NULL DEFAULT 1,
    turn           INTEGER NOT NULL DEFAULT 0,
    status         TEXT NOT NULL DEFAULT 'in_progress'
);
CREATE TABLE IF NOT EXISTS messages (
    session_id INTEGER NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    seq        INTEGER NOT NULL,
    speaker    TEXT NOT NULL,
    text       TEXT NOT NULL,
    PRIMARY KEY (session_id, seq)
);
CREATE TABLE IF NOT EXISTS guidance (
    session_id INTEGER NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    seq        INTEGER NOT NULL,
    turn       INTEGER NOT NULL,
    text       TEXT NOT NULL,
    PRIMARY KEY (session_id, seq)
);
"""

# Session fields a reopen is expected to restore. The Phase 2 evidence metric
# for this enhancement counts how many of these come back exactly.
RESTORED_FIELDS = (
    "task_original", "specified_task", "assistant_role", "user_role",
    "platform", "model_name", "mode", "concise", "turn", "status",
)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class SessionStore:
    """A tiny write-through store for Playground sessions."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # ----- writing -----------------------------------------------------------
    def create_session(self, *, task_original: str, specified_task: str,
                       assistant_role: str, user_role: str, platform: str,
                       model_name: str, mode: str, concise: bool) -> int:
        now = _now()
        cur = self.db.execute(
            "INSERT INTO sessions (created_at, updated_at, task_original,"
            " specified_task, assistant_role, user_role, platform, model_name,"
            " mode, concise) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now, now, task_original, specified_task, assistant_role, user_role,
             platform, model_name, mode, 1 if concise else 0),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def append_message(self, session_id: int, speaker: str, text: str) -> None:
        seq = self.db.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]
        self.db.execute(
            "INSERT INTO messages (session_id, seq, speaker, text) VALUES (?, ?, ?, ?)",
            (session_id, seq, speaker, text),
        )
        self._touch(session_id)

    def add_guidance(self, session_id: int, turn: int, text: str) -> None:
        seq = self.db.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 FROM guidance WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]
        self.db.execute(
            "INSERT INTO guidance (session_id, seq, turn, text) VALUES (?, ?, ?, ?)",
            (session_id, seq, turn, text),
        )
        self._touch(session_id)

    def update_state(self, session_id: int, *, turn: int | None = None,
                     status: str | None = None) -> None:
        sets, values = [], []
        if turn is not None:
            sets.append("turn = ?")
            values.append(turn)
        if status is not None:
            sets.append("status = ?")
            values.append(status)
        if not sets:
            return
        values.append(session_id)
        self.db.execute(
            f"UPDATE sessions SET {', '.join(sets)} WHERE session_id = ?", values
        )
        self._touch(session_id)

    def _touch(self, session_id: int) -> None:
        self.db.execute(
            "UPDATE sessions SET updated_at = ? WHERE session_id = ?",
            (_now(), session_id),
        )
        self.db.commit()

    # ----- reading -------------------------------------------------------------
    def get_session(self, session_id: int) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["concise"] = bool(data["concise"])
        data["messages"] = [
            (r["speaker"], r["text"])
            for r in self.db.execute(
                "SELECT speaker, text FROM messages WHERE session_id = ? ORDER BY seq",
                (session_id,),
            )
        ]
        data["guidance"] = [
            dict(r)
            for r in self.db.execute(
                "SELECT seq, turn, text FROM guidance WHERE session_id = ? ORDER BY seq",
                (session_id,),
            )
        ]
        return data

    def list_sessions(self, limit: int = 50) -> list[dict]:
        rows = self.db.execute(
            "SELECT session_id, created_at, updated_at, task_original,"
            " assistant_role, user_role, turn, status, mode FROM sessions"
            " ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_session(self, session_id: int) -> None:
        self.db.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        self.db.execute("DELETE FROM guidance WHERE session_id = ?", (session_id,))
        self.db.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        self.db.commit()
