from __future__ import annotations

import tempfile
import unittest
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ai_interview.llm import CopilotTurn
from ai_interview.pipeline import InterviewPipeline
from ai_interview.session import InterviewSession, TurnRecord, session_markdown, session_print_html
from ai_interview.store import SessionStore


def _turn(index: int = 1, question: str = "What is a mutex?", answer: str = "A lock.") -> TurnRecord:
    return TurnRecord(
        index=index,
        question=question,
        answer=answer,
        listen_ms=100,
        stt_ms=200,
        llm_ms=300,
        llm_first_ms=80,
        total_ms=500,
        source="spoken",
    )


class SessionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SessionStore(Path(self._tmp.name) / "sessions.db")

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def test_save_reload_and_search(self) -> None:
        session = InterviewSession()
        session.add_turn(_turn())
        session.add_turn(_turn(2, "What is a semaphore?", "A counter."))
        session.active = False
        session.ended_at = session.updated_at
        session.duration_ms = 12000
        self.store.save(session)

        loaded = self.store.get(session.id)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.id, session.id)
        self.assertEqual(len(loaded.turns), 2)
        self.assertEqual(loaded.turns[1].question, "What is a semaphore?")
        self.assertFalse(loaded.active)
        self.assertEqual(loaded.duration_ms, 12000)

        latest = self.store.latest()
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.id, session.id)

        hits = self.store.list_summaries(query="semaphore")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["id"], session.id)
        self.assertIn("semaphore", hits[0]["title"].lower())
        self.assertEqual(hits[0]["match"], "question")

        miss = self.store.list_summaries(query="kubernetes")
        self.assertEqual(miss, [])

    def test_context_round_trip_and_search(self) -> None:
        session = InterviewSession()
        session.set_context(
            role="Staff SWE",
            company="Globex",
            job_description="Lead the platform team.",
            resume="Shipped Kafka pipelines.",
        )
        session.add_turn(_turn())
        self.store.save(session)

        loaded = self.store.get(session.id)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.role, "Staff SWE")
        self.assertEqual(loaded.company, "Globex")
        self.assertIn("Kafka", loaded.resume)
        self.assertEqual(loaded.snapshot()["context"]["company"], "Globex")
        self.assertEqual(loaded.answer_mode, "spoken_45")

        hits = self.store.list_summaries(query="Globex")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["company"], "Globex")
        self.assertEqual(hits[0]["role"], "Staff SWE")
        self.assertEqual(hits[0]["match"], "context")

        markdown = session_markdown(loaded)
        self.assertIn("Role: Staff SWE", markdown)
        self.assertIn("Company: Globex", markdown)
        self.assertNotIn("Kafka pipelines", markdown)
        html = session_print_html(loaded)
        self.assertIn("Staff SWE · Globex", html)

    def test_migrates_legacy_sessions_table(self) -> None:
        import sqlite3

        path = Path(self._tmp.name) / "legacy.db"
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                updated_at TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                duration_ms INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE turns (
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
                source TEXT NOT NULL DEFAULT 'spoken'
            );
            """
        )
        conn.execute(
            "INSERT INTO sessions (id, started_at, ended_at, updated_at, active, duration_ms) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("legacy", "2026-01-01T00:00:00+00:00", None, "2026-01-01T00:00:00+00:00", 1, 0),
        )
        conn.commit()
        conn.close()

        store = SessionStore(path)
        loaded = store.get("legacy")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.role, "")
        self.assertEqual(loaded.answer_mode, "spoken_45")
        loaded.set_context(role="PM", company="Initech")
        store.save(loaded)
        again = store.get("legacy")
        assert again is not None
        self.assertEqual(again.company, "Initech")
        store.close()

    def test_close_stale_and_resumable(self) -> None:
        session = InterviewSession()
        session.add_turn(_turn())
        session.updated_at = (datetime.now(timezone.utc) - timedelta(hours=9)).isoformat()
        self.store.save(session)

        self.assertIsNone(self.store.resumable())
        loaded = self.store.get(session.id)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertFalse(loaded.active)
        self.assertIsNotNone(loaded.ended_at)

    def test_markdown_export(self) -> None:
        session = InterviewSession()
        session.add_turn(_turn())
        markdown = session_markdown(session)
        self.assertIn("# What is a mutex?", markdown)
        self.assertIn("A lock.", markdown)
        self.assertIn("spoken · listen", markdown)
        html = session_print_html(session, autoprint=True)
        self.assertIn("What is a mutex?", html)
        self.assertIn("spoken · listen", html)
        self.assertIn("window.print()", html)

    def test_answer_mode_round_trip(self) -> None:
        session = InterviewSession()
        session.set_answer_mode("star")
        turn = _turn()
        turn.answer_mode = "star"
        session.add_turn(turn)
        self.store.save(session)

        loaded = self.store.get(session.id)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.answer_mode, "spoken_45")
        self.assertEqual(loaded.turns[0].answer_mode, "spoken_45")

    def test_search_tokens_match_snippets_and_escaping(self) -> None:
        mutex = InterviewSession(role="SWE", company="Globex")
        mutex.add_turn(_turn(1, "What is a mutex?", "A lock."))
        mutex.add_turn(_turn(2, "Explain a semaphore.", "A counter used to limit access."))
        mutex.active = False
        mutex.ended_at = mutex.updated_at
        self.store.save(mutex)

        k8s = InterviewSession(company="Initech")
        k8s.add_turn(_turn(1, "What is Kubernetes?", "Orchestration for containers."))
        k8s.active = False
        k8s.ended_at = k8s.updated_at
        self.store.save(k8s)

        both = self.store.list_summaries(query="semaphore globex")
        self.assertEqual([item["id"] for item in both], [mutex.id])
        self.assertIn("semaphore", both[0]["title"].lower())
        self.assertEqual(both[0]["match"], "question")

        case_hits = self.store.list_summaries(query="GLOBEX")
        self.assertEqual(len(case_hits), 1)
        self.assertEqual(case_hits[0]["id"], mutex.id)

        answer_hits = self.store.list_summaries(query="orchestration")
        self.assertEqual(len(answer_hits), 1)
        self.assertEqual(answer_hits[0]["id"], k8s.id)
        self.assertEqual(answer_hits[0]["match"], "answer")
        self.assertIn("Orchestration", answer_hits[0]["preview"])

        self.assertEqual(self.store.list_summaries(query="semaphore initech"), [])
        self.assertEqual(self.store.list_summaries(query="%"), [])
        self.assertEqual(self.store.list_summaries(query="_API_"), [])


class PipelinePersistTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SessionStore(Path(self._tmp.name) / "sessions.db")
        self.events: list[dict] = []

        async def emit(event: dict) -> None:
            self.events.append(event)

        self.pipeline = InterviewPipeline(emit, store=self.store)

    async def asyncTearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    async def test_turn_is_saved_and_resumed(self) -> None:
        self.pipeline._session = InterviewSession()
        self.pipeline._session_mono = 0.0
        turn = CopilotTurn(stage="answer", question="What is REST?", answer="An API style.")
        await self.pipeline._publish(
            turn,
            "What is REST?",
            listen_ms=10,
            stt_ms=20,
            llm_ms=30,
            llm_first_ms=5,
            stt_reused=False,
            streamed_question=True,
            source="typed",
        )
        saved = self.store.latest()
        self.assertIsNotNone(saved)
        assert saved is not None
        self.assertEqual(saved.turns[0].question, "What is REST?")
        self.assertEqual(saved.turns[0].source, "typed")

        resumed_events: list[dict] = []

        async def emit(event: dict) -> None:
            resumed_events.append(event)

        other = InterviewPipeline(emit, store=self.store)
        await other._ensure_session()
        self.assertEqual(other._session.id if other._session else None, saved.id)
        self.assertEqual(resumed_events[0]["type"], "session_resume")
        self.assertEqual(len(other._session.turns) if other._session else 0, 1)

    async def test_new_session_copies_prior_context(self) -> None:
        ended = InterviewSession(role="SWE", company="Acme", resume="Python, Kafka.")
        ended.active = False
        ended.ended_at = ended.updated_at
        self.store.save(ended)

        events: list[dict] = []

        async def emit(event: dict) -> None:
            events.append(event)

        other = InterviewPipeline(emit, store=self.store)
        await other._ensure_session()
        assert other._session is not None
        self.assertNotEqual(other._session.id, ended.id)
        self.assertEqual(other._session.role, "SWE")
        self.assertEqual(other._session.company, "Acme")
        self.assertIn("Kafka", other._session.resume)
        self.assertEqual(events[0]["type"], "session_start")
        self.assertEqual(events[0]["session"]["context"]["company"], "Acme")

    async def test_start_new_session_archives_current_and_keeps_context(self) -> None:
        self.pipeline._session = InterviewSession(role="SWE", company="Acme")
        self.pipeline._session_mono = 0.0
        turn = CopilotTurn(stage="answer", question="What is REST?", answer="An API style.")
        await self.pipeline._publish(
            turn,
            "What is REST?",
            listen_ms=10,
            stt_ms=20,
            llm_ms=30,
            llm_first_ms=5,
            stt_reused=False,
            streamed_question=True,
            source="typed",
        )
        assert self.pipeline._session is not None
        old_id = self.pipeline._session.id
        self.events.clear()
        await self.pipeline.start_new_session()
        types = [event["type"] for event in self.events]
        self.assertNotIn("session_summary", types)
        self.assertEqual(self.events[-1]["type"], "session_start")
        assert self.pipeline._session is not None
        self.assertNotEqual(self.pipeline._session.id, old_id)
        self.assertEqual(self.pipeline._session.turns, [])
        self.assertEqual(self.pipeline._session.company, "Acme")
        archived = self.store.get(old_id)
        self.assertIsNotNone(archived)
        assert archived is not None
        self.assertFalse(archived.active)
        self.assertEqual(len(archived.turns), 1)

    async def test_start_new_session_does_not_resume_store_session(self) -> None:
        active = InterviewSession(role="PM", company="Initech")
        active.add_turn(_turn())
        self.store.save(active)

        events: list[dict] = []

        async def emit(event: dict) -> None:
            events.append(event)

        other = InterviewPipeline(emit, store=self.store)
        await other.start_new_session()
        assert other._session is not None
        self.assertNotEqual(other._session.id, active.id)
        self.assertEqual(other._session.role, "PM")
        loaded = self.store.get(active.id)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertFalse(loaded.active)

    async def test_pending_context_wins_over_copied_session(self) -> None:
        ended = InterviewSession(role="Old role", company="OldCo")
        ended.active = False
        self.store.save(ended)

        events: list[dict] = []

        async def emit(event: dict) -> None:
            events.append(event)

        pipeline = InterviewPipeline(emit, store=self.store)
        await pipeline.set_context(role="Staff SWE", company="Globex", resume="Shipped billing.")
        self.assertIsNone(pipeline._session)
        self.assertEqual(events[-1]["type"], "context_updated")
        await pipeline._ensure_session()
        assert pipeline._session is not None
        self.assertEqual(pipeline._session.role, "Staff SWE")
        self.assertEqual(pipeline._session.company, "Globex")

    async def test_typed_turn_sends_context_in_system_prompt(self) -> None:
        self.pipeline._session = InterviewSession(
            role="Staff SWE",
            company="Acme",
            job_description="Own checkout.",
            resume="Built payments at Stripe.",
        )
        self.pipeline._session_mono = 0.0
        self.pipeline._clients = SimpleNamespace(chat="chat", stt=None)
        captured: dict = {}

        async def fake_copilot(_client, question, **kwargs):
            captured["system_prompt"] = kwargs.get("system_prompt")
            captured["history"] = kwargs.get("history")
            return CopilotTurn(stage="answer", question=question, answer="I scaled payments.")

        from unittest.mock import patch

        with patch("ai_interview.pipeline.copilot_turn", new=fake_copilot):
            await self.pipeline._commit_typed("Tell me about a hard problem.")
        prompt = captured["system_prompt"] or ""
        self.assertIn("Staff SWE", prompt)
        self.assertIn("Acme", prompt)
        self.assertIn("Stripe", prompt)
        self.assertIn("**definition:**", prompt.lower())

    async def test_typed_turn_uses_coding_prompt(self) -> None:
        self.pipeline._session = InterviewSession()
        self.pipeline._session.set_answer_mode("star")
        self.pipeline._session_mono = 0.0
        self.pipeline._clients = SimpleNamespace(chat="chat", stt=None)
        captured: dict = {}

        async def fake_copilot(_client, question, **kwargs):
            captured["system_prompt"] = kwargs.get("system_prompt")
            captured["max_tokens"] = kwargs.get("max_tokens")
            return CopilotTurn(stage="answer", question=question, answer="I scaled payments.")

        from unittest.mock import patch

        with patch("ai_interview.pipeline.copilot_turn", new=fake_copilot):
            await self.pipeline._commit_typed("Tell me about a production incident.")
        prompt = captured["system_prompt"] or ""
        self.assertIn("**definition:**", prompt.lower())
        self.assertNotIn("Situation:", prompt)
        qa = [event for event in self.events if event.get("type") == "qa"][-1]
        self.assertEqual(qa["answer_mode"], "spoken_45")
        assert self.pipeline._session is not None
        self.assertEqual(self.pipeline._session.turns[-1].answer_mode, "spoken_45")

    async def test_answer_mode_persists_on_new_session(self) -> None:
        self.pipeline._session = InterviewSession()
        self.pipeline._session.set_answer_mode("system_design")
        self.pipeline._session_mono = 0.0
        await self.pipeline.start_new_session()
        assert self.pipeline._session is not None
        self.assertEqual(self.pipeline._session.answer_mode, "spoken_45")
        self.assertEqual(self.events[-1]["session"]["answer_mode"], "spoken_45")


if __name__ == "__main__":
    unittest.main()
