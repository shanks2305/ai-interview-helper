from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .llm import TYPED_PROMPT, copilot_turn, new_clients, transcribe_audio
from .session import InterviewSession, TurnRecord, ms_between, utc_now
from .settings import MIN_AUDIO_BYTES, STT_PROVIDER, LLM_MAX_TOKENS, chat_ready, readiness_error

logger = logging.getLogger("uvicorn.error")

Emit = Callable[[dict[str, Any]], Awaitable[None]]
REUSE_EXTRA_BYTES = 12000


def _fold(text: str) -> str:
    cleaned = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", cleaned).casefold().strip()


def _already_handled(text: str, handled: list[str]) -> bool:
    folded = _fold(text)
    if not folded:
        return False
    return any(_fold(item) == folded for item in handled)


class InterviewPipeline:
    def __init__(self, emit: Emit) -> None:
        self._emit = emit
        self._clients = None
        self._chunks: list[bytes] = []
        self._mime_type = "audio/webm"
        self._handled: list[str] = []
        self._work = asyncio.Lock()
        self._turn = asyncio.Lock()
        self._jobs: set[asyncio.Task[None]] = set()
        self._closed = False
        self._session: InterviewSession | None = None
        self._session_mono: float | None = None
        self._listen_mono: float | None = None
        self._prime_task: asyncio.Task[tuple[str, int]] | None = None
        self._prime_bytes = 0

    def session_snapshot(self) -> dict[str, Any] | None:
        if self._session is None:
            return None
        duration = ms_between(self._session_mono or time.perf_counter(), time.perf_counter())
        return self._session.snapshot(duration)

    async def start(self, mime_type: str | None) -> None:
        error = readiness_error()
        if error:
            await self._emit({"type": "error", "message": error})
            return
        async with self._work:
            if self._closed:
                return
            self._chunks.clear()
            self._mime_type = mime_type or "audio/webm"
            self._cancel_prime()
            self._listen_mono = time.perf_counter()
            if self._clients is None:
                self._clients = new_clients()
                if STT_PROVIDER == "local":
                    await self._emit(
                        {
                            "type": "status",
                            "state": "thinking",
                            "detail": "Loading local Whisper…",
                        }
                    )
                    from .stt import ensure_model

                    await asyncio.to_thread(ensure_model)
            await self._ensure_session()
            await self._emit(
                {"type": "status", "state": "listening", "detail": "Mic live · capturing question"}
            )

    async def prime(self) -> None:
        if self._closed or self._clients is None:
            return
        audio = self._audio_bytes()
        mime_type = self._mime_type
        if len(audio) < MIN_AUDIO_BYTES:
            return
        if self._prime_task and not self._prime_task.done():
            return
        self._prime_bytes = len(audio)
        self._prime_task = asyncio.create_task(self._transcribe_snapshot(audio, mime_type))
        await self._emit({"type": "status", "state": "thinking", "detail": "Transcribing…"})

    def add_audio(self, payload: bytes) -> None:
        if payload and not self._closed:
            self._chunks.append(payload)

    async def stop(self) -> None:
        async with self._work:
            listen_ms = ms_between(self._listen_mono or time.perf_counter(), time.perf_counter())
            self._listen_mono = None
            audio = self._audio_bytes()
            mime_type = self._mime_type
            prime = self._prime_task
            prime_bytes = self._prime_bytes
            self._prime_task = None
            self._prime_bytes = 0
            self._chunks.clear()
            closed = self._closed or self._clients is None
        if closed:
            await self._emit({"type": "status", "state": "ready", "detail": "Paused"})
            return
        self._spawn(
            self._commit_clip(audio, mime_type, listen_ms, prime, prime_bytes)
        )

    async def ask_typed(self, text: str) -> None:
        question = (text or "").strip()
        if not question:
            return
        if not chat_ready():
            error = readiness_error() or "Chat model is not configured."
            await self._emit({"type": "error", "message": error})
            return
        self._spawn(self._commit_typed(question))

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._jobs.add(task)

        def _done(done: asyncio.Task[None]) -> None:
            self._jobs.discard(done)
            error = done.exception() if not done.cancelled() else None
            if error:
                logger.exception("background interview turn failed", exc_info=error)

        task.add_done_callback(_done)

    async def _await_jobs(self) -> None:
        jobs = list(self._jobs)
        if jobs:
            await asyncio.gather(*jobs, return_exceptions=True)

    async def end_session(self) -> None:
        await self._await_jobs()
        async with self._work:
            await self._finish_session()

    async def close(self) -> None:
        self._closed = True
        pending = self._cancel_prime()
        if pending is not None:
            try:
                await pending
            except (asyncio.CancelledError, Exception):
                pass
        await self._await_jobs()
        async with self._work:
            await self._finish_session()
            clients = self._clients
            self._clients = None
            self._chunks.clear()
        if clients is not None:
            await clients.close()

    def _audio_bytes(self) -> bytes:
        return b"".join(self._chunks)

    def _cancel_prime(self) -> asyncio.Task[tuple[str, int]] | None:
        task = self._prime_task
        self._prime_task = None
        self._prime_bytes = 0
        if task and not task.done():
            task.cancel()
            return task
        return None

    async def _ensure_session(self) -> None:
        if self._session is not None and self._session.active:
            return
        self._session = InterviewSession()
        self._session_mono = time.perf_counter()
        self._handled.clear()
        await self._emit(
            {
                "type": "session_start",
                "started_at": self._session.started_at,
                "session": self._session.snapshot(0),
            }
        )

    async def _transcribe_snapshot(self, audio: bytes, mime_type: str) -> tuple[str, int]:
        if self._clients is None:
            return "", 0
        started = time.perf_counter()
        text = await transcribe_audio(self._clients.stt, audio, mime_type)
        elapsed = ms_between(started, time.perf_counter())
        return (text or "").strip(), elapsed

    async def _commit_clip(
        self,
        audio: bytes,
        mime_type: str,
        listen_ms: int,
        prime: asyncio.Task[tuple[str, int]] | None,
        prime_bytes: int,
    ) -> None:
        async with self._turn:
            if self._closed or self._clients is None:
                await self._emit({"type": "status", "state": "ready", "detail": "Paused"})
                return
            if len(audio) < MIN_AUDIO_BYTES:
                if prime and not prime.done():
                    prime.cancel()
                await self._emit(
                    {"type": "status", "state": "ready", "detail": "Paused · no speech captured"}
                )
                return

            transcript, stt_ms, reused = await self._resolve_transcript(
                audio, mime_type, prime, prime_bytes
            )
            if not transcript:
                await self._emit(
                    {"type": "status", "state": "ready", "detail": "Paused · no speech captured"}
                )
                return

            logger.info("transcript=%s reused=%s stt_ms=%s", transcript[:180], reused, stt_ms)
            if _already_handled(transcript, self._handled):
                await self._emit({"type": "status", "state": "ready", "detail": "Paused"})
                return

            streamed_question = False
            await self._emit({"type": "question", "text": transcript, "valid": True})
            await self._emit({"type": "status", "state": "thinking", "detail": "Drafting answer"})

            llm_first_ms = 0
            llm_started = time.perf_counter()

            async def on_question(text: str) -> None:
                nonlocal streamed_question
                if streamed_question or _already_handled(text, self._handled):
                    return
                streamed_question = True
                await self._emit({"type": "question", "text": text, "valid": True})

            async def on_answer(text: str) -> None:
                nonlocal llm_first_ms
                if not llm_first_ms:
                    llm_first_ms = ms_between(llm_started, time.perf_counter())
                await self._emit({"type": "answer_delta", "text": text})

            try:
                turn = await copilot_turn(
                    self._clients.chat,
                    transcript,
                    self._handled,
                    on_question=on_question,
                    on_answer=on_answer,
                )
            except Exception:
                logger.exception("llm turn failed")
                await self._emit({"type": "error", "message": "The LLM could not draft a reply."})
                await self._emit({"type": "status", "state": "ready", "detail": "Paused"})
                return
            llm_ms = ms_between(llm_started, time.perf_counter())
            await self._publish(
                turn,
                transcript,
                listen_ms=listen_ms,
                stt_ms=stt_ms,
                llm_ms=llm_ms,
                llm_first_ms=llm_first_ms or llm_ms,
                stt_reused=reused,
                streamed_question=streamed_question,
            )

    async def _commit_typed(self, question: str) -> None:
        async with self._turn:
            if self._closed:
                return
            if self._clients is None:
                self._clients = new_clients()
            await self._ensure_session()
            if _already_handled(question, self._handled):
                await self._emit({"type": "status", "state": "ready", "detail": "Already answered"})
                return

            await self._emit({"type": "question", "text": question, "valid": True, "source": "typed"})
            await self._emit(
                {"type": "status", "state": "thinking", "detail": "Drafting typed question"}
            )

            token_limit = 2048
            if LLM_MAX_TOKENS is not None:
                token_limit = max(LLM_MAX_TOKENS, 2048)

            llm_first_ms = 0
            llm_started = time.perf_counter()

            async def on_answer(text: str) -> None:
                nonlocal llm_first_ms
                if not llm_first_ms:
                    llm_first_ms = ms_between(llm_started, time.perf_counter())
                await self._emit({"type": "answer_delta", "text": text})

            try:
                turn = await copilot_turn(
                    self._clients.chat,
                    question,
                    self._handled,
                    on_answer=on_answer,
                    system_prompt=TYPED_PROMPT,
                    max_tokens=token_limit,
                )
            except Exception:
                logger.exception("typed llm turn failed")
                await self._emit({"type": "error", "message": "The LLM could not draft a reply."})
                await self._emit({"type": "status", "state": "ready", "detail": "Paused"})
                return

            llm_ms = ms_between(llm_started, time.perf_counter())
            turn.question = question
            await self._publish(
                turn,
                question,
                listen_ms=0,
                stt_ms=0,
                llm_ms=llm_ms,
                llm_first_ms=llm_first_ms or llm_ms,
                stt_reused=False,
                streamed_question=True,
            )

    async def _resolve_transcript(
        self,
        audio: bytes,
        mime_type: str,
        prime: asyncio.Task[tuple[str, int]] | None,
        prime_bytes: int,
    ) -> tuple[str, int, bool]:
        extra = len(audio) - prime_bytes if prime_bytes else len(audio)
        can_reuse = bool(prime) and extra <= REUSE_EXTRA_BYTES

        if prime and can_reuse:
            try:
                text, stt_ms = await prime
                if text:
                    return text, stt_ms, True
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("primed transcription failed")
        elif prime and not prime.done():
            prime.cancel()

        try:
            text, stt_ms = await self._transcribe_snapshot(audio, mime_type)
        except Exception:
            logger.exception("transcription failed")
            await self._emit(
                {"type": "error", "message": "Could not transcribe the interview audio."}
            )
            await self._emit({"type": "status", "state": "ready", "detail": "Paused"})
            return "", 0, False
        return text, stt_ms, False

    async def _publish(
        self,
        turn,
        transcript: str,
        *,
        listen_ms: int,
        stt_ms: int,
        llm_ms: int,
        llm_first_ms: int,
        stt_reused: bool,
        streamed_question: bool,
    ) -> None:
        text = (turn.question or turn.latest_speech or transcript).strip()
        answer = (turn.answer or "").strip()
        if not text:
            await self._emit({"type": "status", "state": "ready", "detail": "Paused"})
            return
        if _already_handled(text, self._handled):
            await self._emit({"type": "status", "state": "ready", "detail": "Paused"})
            return

        self._handled.append(text)
        if len(self._handled) > 40:
            self._handled = self._handled[-40:]

        if not streamed_question:
            await self._emit({"type": "question", "text": text, "valid": True})
        if answer:
            await self._emit({"type": "answer", "text": answer})
            await self._emit({"type": "qa", "question": text, "answer": answer, "valid": True})
            logger.info("answer=%s", answer[:180])
        else:
            await self._emit(
                {"type": "qa", "question": text, "answer": "(no answer drafted)", "valid": True}
            )

        record = TurnRecord(
            index=len(self._session.turns) + 1 if self._session else 1,
            question=text,
            answer=answer or "(no answer drafted)",
            listen_ms=listen_ms,
            stt_ms=stt_ms,
            llm_ms=llm_ms,
            llm_first_ms=llm_first_ms,
            total_ms=stt_ms + llm_ms,
            stt_reused=stt_reused,
        )
        if self._session is not None:
            self._session.add_turn(record)
            duration = ms_between(self._session_mono or time.perf_counter(), time.perf_counter())
            await self._emit(
                {
                    "type": "turn_stats",
                    **record.as_dict(),
                    "session": self._session.snapshot(duration),
                }
            )
        await self._emit(
            {"type": "status", "state": "ready", "detail": "Paused · listen for the next question"}
        )

    async def _finish_session(self) -> None:
        session = self._session
        if session is None or not session.active:
            return
        session.active = False
        session.ended_at = utc_now()
        duration = ms_between(self._session_mono or time.perf_counter(), time.perf_counter())
        await self._emit(
            {
                "type": "session_summary",
                "started_at": session.started_at,
                "ended_at": session.ended_at,
                "session": session.snapshot(duration),
            }
        )
        self._session = None
        self._session_mono = None
        self._handled.clear()
