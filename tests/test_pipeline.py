from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ai_interview.pipeline import REUSE_EXTRA_BYTES, InterviewPipeline
from ai_interview.session import InterviewSession, TurnRecord


class ResolveTranscriptReuseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.events: list[dict] = []

        async def emit(event: dict) -> None:
            self.events.append(event)

        self.pipeline = InterviewPipeline(emit)
        self.pipeline._clients = SimpleNamespace(stt="stt")

    async def _prime(self, text: str, stt_ms: int = 17) -> asyncio.Task[tuple[str, int]]:
        async def primed() -> tuple[str, int]:
            return text, stt_ms

        task = asyncio.create_task(primed())
        await asyncio.sleep(0)
        return task

    async def test_reuses_prime_when_extra_bytes_within_budget(self) -> None:
        prime_bytes = 8000
        audio = b"x" * (prime_bytes + REUSE_EXTRA_BYTES)
        prime = await self._prime("What is a mutex?")
        with patch("ai_interview.pipeline.transcribe_audio", new=AsyncMock()) as transcribe:
            text, stt_ms, reused = await self.pipeline._resolve_transcript(
                audio, "audio/webm", prime, prime_bytes
            )
        self.assertEqual(text, "What is a mutex?")
        self.assertEqual(stt_ms, 17)
        self.assertTrue(reused)
        transcribe.assert_not_called()

    async def test_retranscribes_when_extra_exceeds_budget(self) -> None:
        prime_bytes = 8000
        audio = b"x" * (prime_bytes + REUSE_EXTRA_BYTES + 1)
        prime = await self._prime("stale prime")
        with patch(
            "ai_interview.pipeline.transcribe_audio",
            new=AsyncMock(return_value="  full window question  "),
        ) as transcribe:
            text, stt_ms, reused = await self.pipeline._resolve_transcript(
                audio, "audio/webm", prime, prime_bytes
            )
        self.assertEqual(text, "full window question")
        self.assertFalse(reused)
        transcribe.assert_awaited_once()

    async def test_retranscribes_when_prime_text_is_empty(self) -> None:
        prime_bytes = 4000
        audio = b"x" * (prime_bytes + 10)
        prime = await self._prime("")
        with patch(
            "ai_interview.pipeline.transcribe_audio",
            new=AsyncMock(return_value="retry transcript"),
        ):
            text, _stt_ms, reused = await self.pipeline._resolve_transcript(
                audio, "audio/webm", prime, prime_bytes
            )
        self.assertEqual(text, "retry transcript")
        self.assertFalse(reused)

    async def test_cancels_in_flight_prime_when_window_grew(self) -> None:
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def slow_prime() -> tuple[str, int]:
            started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return "should not reuse", 1

        prime = asyncio.create_task(slow_prime())
        await started.wait()
        audio = b"x" * (100 + REUSE_EXTRA_BYTES + 50)
        with patch(
            "ai_interview.pipeline.transcribe_audio",
            new=AsyncMock(return_value="late speech"),
        ):
            text, _stt_ms, reused = await self.pipeline._resolve_transcript(
                audio, "audio/webm", prime, 100
            )
        self.assertEqual(text, "late speech")
        self.assertFalse(reused)
        with self.assertRaises(asyncio.CancelledError):
            await prime
        self.assertTrue(cancelled.is_set())


class PipelineSnapshotTests(unittest.TestCase):
    def test_session_snapshot_is_none_before_start(self) -> None:
        pipeline = InterviewPipeline(AsyncMock())
        self.assertIsNone(pipeline.session_snapshot())

    def test_session_snapshot_includes_turns(self) -> None:
        pipeline = InterviewPipeline(AsyncMock())
        pipeline._session = InterviewSession()
        pipeline._session_mono = 0.0
        pipeline._session.add_turn(
            TurnRecord(
                index=1,
                question="What is a mutex?",
                answer="A lock.",
                listen_ms=1,
                stt_ms=2,
                llm_ms=3,
                llm_first_ms=1,
                total_ms=5,
                stt_reused=True,
            )
        )
        snap = pipeline.session_snapshot()
        assert snap is not None
        self.assertEqual(snap["stats"]["questions"], 1)
        self.assertTrue(snap["turns"][0]["stt_reused"])
