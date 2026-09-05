from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ai_interview.llm import CopilotTurn
from ai_interview.pipeline import InterviewPipeline
from ai_interview.session import InterviewSession


class PipelineSkipTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.events: list[dict] = []

        async def emit(event: dict) -> None:
            self.events.append(event)

        self.pipeline = InterviewPipeline(emit)
        self.pipeline._session = InterviewSession()
        self.pipeline._session_mono = 0.0

    async def test_not_a_question_is_a_retryable_skip(self) -> None:
        turn = CopilotTurn(
            stage="not_a_question",
            question="hello there",
            latest_speech="hello there",
        )
        await self.pipeline._publish(
            turn,
            "hello there",
            listen_ms=10,
            stt_ms=20,
            llm_ms=30,
            llm_first_ms=5,
            stt_reused=False,
            streamed_question=True,
            source="spoken",
        )
        self.assertEqual(self.pipeline._session.turns, [])
        skip = self.events[0]
        self.assertEqual(skip["type"], "skip")
        self.assertEqual(skip["reason"], "not_a_question")
        self.assertTrue(skip["retryable"])
        self.assertEqual(self.events[1]["type"], "status")
        self.assertIn("not a question", self.events[1]["detail"].lower())

    async def test_answered_questions_drive_duplicate_skips(self) -> None:
        from ai_interview.session import TurnRecord

        self.pipeline._session.add_turn(
            TurnRecord(
                index=1,
                question="What is a REST API?",
                answer="A REST API is...",
                listen_ms=1,
                stt_ms=1,
                llm_ms=1,
                llm_first_ms=1,
                total_ms=2,
            )
        )
        await self.pipeline._commit_typed("What's a REST API?")
        skip = next(event for event in self.events if event.get("type") == "skip")
        self.assertEqual(skip["reason"], "duplicate")
        self.assertFalse(skip["retryable"])
        self.assertEqual(len(self.pipeline._session.turns), 1)

    async def test_transcribe_snapshot_emits_partial_question(self) -> None:
        self.pipeline._clients = SimpleNamespace(stt="stt")
        with patch(
            "ai_interview.pipeline.transcribe_audio",
            new=AsyncMock(return_value="  What is a mutex? "),
        ):
            text, elapsed = await self.pipeline._transcribe_snapshot(
                b"audio", "audio/webm", emit_partial=True
            )
        self.assertEqual(text, "What is a mutex?")
        self.assertGreaterEqual(elapsed, 0)
        partial = self.events[0]
        self.assertEqual(partial["type"], "partial_question")
        self.assertEqual(partial["text"], "What is a mutex?")
        self.assertEqual(partial["source"], "spoken")

    async def test_commit_clip_streams_partial_before_skip(self) -> None:
        self.pipeline._clients = SimpleNamespace(stt="stt")

        async def resolve(_audio, _mime_type, _prime, _prime_bytes):
            return "um", 12, False

        self.pipeline._resolve_transcript = resolve  # type: ignore[method-assign]
        await self.pipeline._commit_clip(b"x" * 3000, "audio/webm", 40, None, 0)
        types = [event["type"] for event in self.events]
        self.assertEqual(types[0], "partial_question")
        self.assertEqual(self.events[0]["text"], "um")
        self.assertIn("skip", types)


if __name__ == "__main__":
    unittest.main()
