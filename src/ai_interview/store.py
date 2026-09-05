from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

from .paths import data_dir
from .modes import DEFAULT_ANSWER_MODE, normalize_answer_mode
from .session import InterviewSession, TurnRecord, utc_now

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    updated_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    role TEXT NOT NULL DEFAULT '',
    company TEXT NOT NULL DEFAULT '',
    job_description TEXT NOT NULL DEFAULT '',
    resume TEXT NOT NULL DEFAULT '',
    answer_mode TEXT NOT NULL DEFAULT 'spoken_45'
);

CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    listen_ms INTEGER NOT NULL,
    stt_ms INTEGER NOT NULL,
    llm_ms INTEGER NOT NULL,
    llm_first_ms INTEGER NOT NULL,
    total_ms INTEGER NOT NULL,
    stt_reused INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'spoken',
    answer_mode TEXT NOT NULL DEFAULT 'spoken_45',
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, idx);
"""

_SESSION_CONTEXT_COLUMNS = (
    ("role", "TEXT NOT NULL DEFAULT ''"),
    ("company", "TEXT NOT NULL DEFAULT ''"),
    ("job_description", "TEXT NOT NULL DEFAULT ''"),
    ("resume", "TEXT NOT NULL DEFAULT ''"),
    ("answer_mode", "TEXT NOT NULL DEFAULT 'spoken_45'"),
)

_TURN_COLUMNS = (("answer_mode", "TEXT NOT NULL DEFAULT 'spoken_45'"),)


class SessionStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _migrate(self) -> None:
        existing = {
            str(row["name"]) for row in self._conn.execute("PRAGMA table_info(sessions)")
        }
        for name, spec in _SESSION_CONTEXT_COLUMNS:
            if name not in existing:
                self._conn.execute(f"ALTER TABLE sessions ADD COLUMN {name} {spec}")
        turn_cols = {
            str(row["name"]) for row in self._conn.execute("PRAGMA table_info(turns)")
        }
        for name, spec in _TURN_COLUMNS:
            if name not in turn_cols:
                self._conn.execute(f"ALTER TABLE turns ADD COLUMN {name} {spec}")

    def save(self, session: InterviewSession) -> None:
        payload = (
            session.id,
            session.started_at,
            session.ended_at,
            session.updated_at or utc_now(),
            1 if session.active else 0,
            int(session.duration_ms or 0),
            session.role or "",
            session.company or "",
            session.job_description or "",
            session.resume or "",
            normalize_answer_mode(session.answer_mode),
        )
        turns = [
            (
                session.id,
                turn.index,
                turn.question,
                turn.answer,
                turn.listen_ms,
                turn.stt_ms,
                turn.llm_ms,
                turn.llm_first_ms,
                turn.total_ms,
                1 if turn.stt_reused else 0,
                turn.source or "spoken",
                normalize_answer_mode(turn.answer_mode),
            )
            for turn in session.turns
        ]
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO sessions (
                    id, started_at, ended_at, updated_at, active, duration_ms,
                    role, company, job_description, resume, answer_mode
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    started_at = excluded.started_at,
                    ended_at = excluded.ended_at,
                    updated_at = excluded.updated_at,
                    active = excluded.active,
                    duration_ms = excluded.duration_ms,
                    role = excluded.role,
                    company = excluded.company,
                    job_description = excluded.job_description,
                    resume = excluded.resume,
                    answer_mode = excluded.answer_mode
                """,
                payload,
            )
            self._conn.execute("DELETE FROM turns WHERE session_id = ?", (session.id,))
            self._conn.executemany(
                """
                INSERT INTO turns (
                    session_id, idx, question, answer, listen_ms, stt_ms, llm_ms,
                    llm_first_ms, total_ms, stt_reused, source, answer_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                turns,
            )
            self._conn.commit()

    def get(self, session_id: str) -> InterviewSession | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            turn_rows = []
            if row is not None:
                turn_rows = self._conn.execute(
                    "SELECT * FROM turns WHERE session_id = ? ORDER BY idx ASC",
                    (session_id,),
                ).fetchall()
        if row is None:
            return None
        return _session_from_rows(row, turn_rows)

    def latest(self) -> InterviewSession | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions ORDER BY updated_at DESC, started_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return self.get(str(row["id"]))

    def close_stale(self) -> None:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM sessions WHERE active = 1").fetchall()
        now = utc_now()
        for row in rows:
            session = self.get(str(row["id"]))
            if session is None or not session.is_stale():
                continue
            session.active = False
            session.ended_at = session.ended_at or now
            session.updated_at = now
            self.save(session)

    def resumable(self) -> InterviewSession | None:
        session = self.latest()
        if session is None or not session.active:
            return None
        if session.is_stale():
            session.active = False
            session.ended_at = session.ended_at or utc_now()
            session.updated_at = utc_now()
            self.save(session)
            return None
        return session

    def list_summaries(self, query: str = "", limit: int = 80) -> list[dict[str, Any]]:
        like = _like_pattern(query)
        params: list[Any] = []
        where = ""
        if like:
            where = """
                WHERE s.role LIKE ? ESCAPE '\\'
                   OR s.company LIKE ? ESCAPE '\\'
                   OR s.id IN (
                    SELECT session_id FROM turns
                    WHERE question LIKE ? ESCAPE '\\' OR answer LIKE ? ESCAPE '\\'
                )
            """
            params.extend([like, like, like, like])
        params.append(max(1, min(int(limit), 200)))
        sql = f"""
            SELECT
                s.id,
                s.started_at,
                s.ended_at,
                s.updated_at,
                s.active,
                s.duration_ms,
                s.role,
                s.company,
                COUNT(t.id) AS questions,
                (
                    SELECT t2.question FROM turns t2
                    WHERE t2.session_id = s.id
                    ORDER BY t2.idx ASC
                    LIMIT 1
                ) AS title,
                (
                    SELECT t3.answer FROM turns t3
                    WHERE t3.session_id = s.id
                    ORDER BY t3.idx DESC
                    LIMIT 1
                ) AS preview
            FROM sessions s
            LEFT JOIN turns t ON t.session_id = s.id
            {where}
            GROUP BY s.id
            ORDER BY s.updated_at DESC, s.started_at DESC
            LIMIT ?
        """
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        summaries: list[dict[str, Any]] = []
        for row in rows:
            title = (row["title"] or "").strip() or "Empty session"
            if len(title) > 72:
                title = title[:71].rstrip() + "…"
            preview = (row["preview"] or "").strip().replace("\n", " ")
            if len(preview) > 140:
                preview = preview[:139].rstrip() + "…"
            summaries.append(
                {
                    "id": row["id"],
                    "started_at": row["started_at"],
                    "ended_at": row["ended_at"],
                    "updated_at": row["updated_at"],
                    "active": bool(row["active"]),
                    "duration_ms": int(row["duration_ms"] or 0),
                    "questions": int(row["questions"] or 0),
                    "title": title,
                    "preview": preview,
                    "role": (row["role"] or "").strip(),
                    "company": (row["company"] or "").strip(),
                }
            )
        return summaries


_store: SessionStore | None = None
_store_lock = threading.Lock()


def get_store() -> SessionStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = SessionStore(data_dir() / "sessions.db")
        return _store


def reset_store(store: SessionStore | None = None) -> None:
    global _store
    with _store_lock:
        _store = store


def _session_from_rows(row: sqlite3.Row, turn_rows: list[sqlite3.Row]) -> InterviewSession:
    turns = [
        TurnRecord(
            index=int(item["idx"] or 0),
            question=item["question"] or "",
            answer=item["answer"] or "",
            listen_ms=int(item["listen_ms"] or 0),
            stt_ms=int(item["stt_ms"] or 0),
            llm_ms=int(item["llm_ms"] or 0),
            llm_first_ms=int(item["llm_first_ms"] or 0),
            total_ms=int(item["total_ms"] or 0),
            stt_reused=bool(item["stt_reused"]),
            source=item["source"] or "spoken",
            answer_mode=normalize_answer_mode(_row_str(item, "answer_mode") or DEFAULT_ANSWER_MODE),
        )
        for item in turn_rows
    ]
    return InterviewSession(
        id=str(row["id"]),
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        updated_at=row["updated_at"] or row["started_at"],
        turns=turns,
        active=bool(row["active"]),
        duration_ms=int(row["duration_ms"] or 0),
        role=_row_str(row, "role"),
        company=_row_str(row, "company"),
        job_description=_row_str(row, "job_description"),
        resume=_row_str(row, "resume"),
        answer_mode=normalize_answer_mode(_row_str(row, "answer_mode") or DEFAULT_ANSWER_MODE),
    )


def _row_str(row: sqlite3.Row, key: str) -> str:
    try:
        return str(row[key] or "")
    except (IndexError, KeyError):
        return ""


def _like_pattern(query: str) -> str:
    text = (query or "").strip()
    if not text:
        return ""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
