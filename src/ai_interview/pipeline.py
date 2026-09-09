from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .classify import DETAILS, ClipVerdict, classify_clip, resolve_turn_mode
from .llm import build_system_prompt, copilot_turn, new_clients, transcribe_audio
from .modes import (
    DEFAULT_ANSWER_MODE,
    max_tokens_for_mode,
    normalize_answer_mode,
    prompt_for_mode,
    redraft_hint,
    redraft_mode_override,
)
from .session import InterviewSession, TurnRecord, ms_between, normalize_context, utc_now
from .settings import MIN_AUDIO_BYTES, STT_PROVIDER, chat_ready, readiness_error
from .store import SessionStore, get_store
from .talking_points import extract_talking_points

logger = logging.getLogger("uvicorn.error")

Emit = Callable[[dict[str, Any]], Awaitable[None]]
REUSE_EXTRA_BYTES = 12000


class InterviewPipeline:
    def __init__(self, emit: Emit, store: SessionStore | None = None) -> None:
        self._emit = emit
        self._given_store = store
        self._clients = None
        self._chunks: list[bytes] = []
        self._mime_type = "audio/webm"
        self._work = asyncio.Lock()
        self._session_gate = asyncio.Lock()
        self._turn = asyncio.Lock()
        self._jobs: set[asyncio.Task[None]] = set()
        self._closed = False
        self._session: InterviewSession | None = None
        self._session_mono: float | None = None
        self._session_base_ms = 0
        self._listen_mono: float | None = None
        self._prime_task: asyncio.Task[tuple[str, int]] | None = None
        self._prime_bytes = 0
        self._pending_context: dict[str, str] | None = None
        self._pending_answer_mode: str | None = None

    def session_snapshot(self) -> dict[str, Any] | None:
        if self._session is None:
            return None
        return self._session.snapshot(self._elapsed_ms())

    def _store(self) -> SessionStore:
        return self._given_store if self._given_store is not None else get_store()

    def _elapsed_ms(self) -> int:
        live = ms_between(self._session_mono or time.perf_counter(), time.perf_counter())
        return self._session_base_ms + live

    def _forget_session(self) -> None:
        self._session = None
        self._session_mono = None
        self._session_base_ms = 0

    def _session_still_live(self) -> bool:
        session = self._session
        if session is None or not session.active:
            return False
        stored = self._store().get(session.id)
        if stored is not None and not stored.active:
            self._forget_session()
            return False
        return True

    def drop_session(self, session_id: str) -> None:
        session = self._session
        if session is not None and session.id == session_id:
            self._forget_session()

    def delete_stored_session(self, session_id: str) -> bool:
        sid = (session_id or "").strip()
        if not sid:
            return False
        store = self._store()
        if store.get(sid) is None:
            return False
        store.delete(sid)
        self.drop_session(sid)
        return True

    def drop_stale_session(self) -> None:
        self._session_still_live()

    def seed_pending(
        self,
        *,
        context: dict[str, str] | None = None,
        answer_mode: str | None = None,
    ) -> None:
        if context and any(str(value or "").strip() for value in context.values()):
            if self._pending_context is None:
                self._pending_context = dict(context)
        if answer_mode is not None and self._pending_answer_mode is None:
            self._pending_answer_mode = normalize_answer_mode(answer_mode)

    def _persist(self) -> None:
        session = self._session
        if session is None:
            return
        if session.active:
            stored = self._store().get(session.id)
            if stored is not None and not stored.active:
                logger.info("session %s already ended elsewhere; not reviving", session.id)
                self._forget_session()
                return
        session.duration_ms = self._elapsed_ms()
        session.updated_at = utc_now()
        try:
            self._store().save(session)
        except Exception:
            logger.exception("could not persist interview session")

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
                {"type": "status", "state": "listening", "detail": "Capturing question"}
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
        self._prime_task = asyncio.create_task(
            self._transcribe_snapshot(audio, mime_type, emit_partial=True)
        )
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

    async def redraft(
        self,
        *,
        instruction: str | None = None,
        mode: str | None = None,
        question: str | None = None,
    ) -> None:
        if not chat_ready():
            error = readiness_error() or "Chat model is not configured."
            await self._emit({"type": "error", "message": error})
            return
        self._spawn(
            self._commit_redraft(
                instruction=instruction,
                mode=mode,
                question=question,
            )
        )

    async def revise_question(self, text: str) -> None:
        question = (text or "").strip()
        if not question:
            return
        await self.redraft(question=question, instruction="again")

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
            async with self._session_gate:
                await self._finish_session()

    async def start_new_session(self) -> None:
        await self._await_jobs()
        async with self._work:
            async with self._session_gate:
                prior = self._session
                if self._session is not None and self._session.active:
                    await self._finish_session(emit_summary=False)
                else:
                    self._forget_session()
                    self._archive_resumable()
                    if prior is None:
                        prior = self._store().latest()
                await self._open_session(resume=False, prior=prior)

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
            async with self._session_gate:
                await self._finish_session()
                clients = self._clients
                self._clients = None
                self._chunks.clear()
        if clients is not None:
            await clients.close()

    def _audio_bytes(self) -> bytes:
        return b"".join(self._chunks)

    def _interview_history(self, *, exclude_last: bool = False) -> list[tuple[str, str]]:
        if self._session is None:
            return []
        pairs = self._session.recent_qa()
        if exclude_last and pairs:
            return pairs[:-1]
        return pairs

    def _current_answer_mode(self) -> str:
        if self._session is not None:
            return normalize_answer_mode(self._session.answer_mode)
        if self._pending_answer_mode is not None:
            return normalize_answer_mode(self._pending_answer_mode)
        return DEFAULT_ANSWER_MODE

    def _turn_mode(
        self,
        question: str,
        *,
        source: str,
        override: str | None = None,
    ) -> tuple[str, str]:
        if override:
            mode = normalize_answer_mode(override)
            kind = resolve_turn_mode("auto", question, source=source)[1]
            if mode != "auto":
                return mode, kind
        return resolve_turn_mode(self._current_answer_mode(), question, source=source)

    def _copilot_prompt(self, *, source: str, mode: str | None = None) -> str:
        base = prompt_for_mode(mode or self._current_answer_mode(), source=source)
        session = self._session
        if session is not None:
            return build_system_prompt(base, **session.context_payload())
        pending = self._pending_context or {}
        if not any(pending.values()):
            return base
        return build_system_prompt(base, **pending)

    def _last_turn(self):
        if self._session is None or not self._session.turns:
            return None
        return self._session.turns[-1]

    def _apply_pending_context(self, session: InterviewSession) -> None:
        if self._pending_context is not None:
            session.set_context(self._pending_context)
            self._pending_context = None
        if self._pending_answer_mode is not None:
            session.set_answer_mode(self._pending_answer_mode)
            self._pending_answer_mode = None

    async def set_context(self, data: dict[str, Any] | None = None, **fields: Any) -> None:
        payload = normalize_context(data, **fields)
        session = self._session if self._session_still_live() else None
        if session is not None:
            session.set_context(payload)
            self._persist()
            await self._emit(
                {
                    "type": "context_updated",
                    "context": session.context_payload(),
                    "session": session.snapshot(self._elapsed_ms()),
                }
            )
            return
        self._pending_context = payload
        await self._emit(
            {
                "type": "context_updated",
                "context": payload,
                "session": self.session_snapshot(),
            }
        )

    async def set_answer_mode(self, mode: str | None) -> None:
        value = normalize_answer_mode(mode)
        session = self._session if self._session_still_live() else None
        if session is not None:
            session.set_answer_mode(value)
            self._persist()
            await self._emit(
                {
                    "type": "answer_mode_updated",
                    "answer_mode": value,
                    "session": session.snapshot(self._elapsed_ms()),
                }
            )
            return
        self._pending_answer_mode = value
        await self._emit(
            {
                "type": "answer_mode_updated",
                "answer_mode": value,
                "session": self.session_snapshot(),
            }
        )

    def _answered_questions(self) -> list[str]:
        if self._session is None:
            return []
        return [turn.question for turn in self._session.turns if (turn.question or "").strip()]

    async def _emit_skip(self, verdict: ClipVerdict, *, source: str) -> None:
        logger.info("skip reason=%s text=%s", verdict.reason, (verdict.text or "")[:180])
        await self._emit(verdict.as_event(source=source))
        await self._emit({"type": "status", "state": "ready", "detail": verdict.detail})

    async def _emit_partial_question(self, text: str, *, source: str = "spoken") -> None:
        question = (text or "").strip()
        if not question:
            return
        await self._emit({"type": "partial_question", "text": question, "source": source})

    def _cancel_prime(self) -> asyncio.Task[tuple[str, int]] | None:
        task = self._prime_task
        self._prime_task = None
        self._prime_bytes = 0
        if task and not task.done():
            task.cancel()
            return task
        return None

    async def _ensure_session(self) -> None:
        async with self._session_gate:
            if self._session_still_live():
                return
            await self._open_session(resume=True)

    def _archive_resumable(self) -> None:
        leftover = self._store().resumable()
        if leftover is None:
            return
        leftover.active = False
        leftover.ended_at = leftover.ended_at or utc_now()
        leftover.updated_at = utc_now()
        try:
            self._store().save(leftover)
        except Exception:
            logger.exception("could not archive leftover interview session")

    async def _open_session(
        self,
        *,
        resume: bool,
        prior: InterviewSession | None = None,
    ) -> None:
        if resume:
            resumed = self._store().resumable()
            if resumed is not None:
                self._session = resumed
                self._apply_pending_context(resumed)
                self._session_base_ms = resumed.duration_ms
                self._session_mono = time.perf_counter()
                self._persist()
                await self._emit(
                    {
                        "type": "session_resume",
                        "started_at": self._session.started_at,
                        "session": self._session.snapshot(self._elapsed_ms()),
                    }
                )
                return
        else:
            self._archive_resumable()
        if prior is None:
            prior = self._store().latest()
        self._session = InterviewSession()
        self._session.copy_context_from(prior)
        self._apply_pending_context(self._session)
        self._session_base_ms = 0
        self._session_mono = time.perf_counter()
        self._persist()
        await self._emit(
            {
                "type": "session_start",
                "started_at": self._session.started_at,
                "session": self._session.snapshot(0),
            }
        )

    async def _transcribe_snapshot(
        self,
        audio: bytes,
        mime_type: str,
        *,
        emit_partial: bool = False,
    ) -> tuple[str, int]:
        if self._clients is None:
            return "", 0
        started = time.perf_counter()
        text = await transcribe_audio(self._clients.stt, audio, mime_type)
        elapsed = ms_between(started, time.perf_counter())
        transcript = (text or "").strip()
        if emit_partial:
            await self._emit_partial_question(transcript)
        return transcript, elapsed

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
            await self._emit_partial_question(transcript)
            verdict = classify_clip(transcript, self._answered_questions())
            if verdict.action == "skip":
                await self._emit_skip(verdict, source="spoken")
                return

            question = verdict.text or transcript
            await self._draft_answer(
                question,
                source="spoken",
                listen_ms=listen_ms,
                stt_ms=stt_ms,
                stt_reused=reused,
            )

    async def _commit_typed(self, question: str) -> None:
        async with self._turn:
            if self._closed:
                return
            await self._ensure_session()
            verdict = classify_clip(question, self._answered_questions(), typed=True)
            if verdict.action == "skip":
                await self._emit_skip(verdict, source="typed")
                return
            if self._clients is None:
                self._clients = new_clients()
            await self._draft_answer(question, source="typed")

    async def _commit_redraft(
        self,
        *,
        instruction: str | None,
        mode: str | None,
        question: str | None,
    ) -> None:
        async with self._turn:
            if self._closed:
                return
            await self._ensure_session()
            last = self._last_turn()
            text = (question or (last.question if last else "") or "").strip()
            if not text:
                await self._emit({"type": "error", "message": "There is no question to rewrite yet."})
                return
            if self._clients is None:
                self._clients = new_clients()
            previous = (last.answer if last else "") or ""
            hint = redraft_hint(instruction)
            override = redraft_mode_override(instruction, mode)
            if previous and previous != "(no answer drafted)":
                user = f"{text}\n\nPrevious draft:\n{previous}\n\nRewrite instruction: {hint}"
            else:
                user = f"{text}\n\nRewrite instruction: {hint}"
            await self._draft_answer(
                text,
                source=last.source if last else "typed",
                user_content=user,
                mode_override=override,
                replace_last=last is not None,
                listen_ms=last.listen_ms if last else 0,
                stt_ms=0,
                stt_reused=False,
            )

    async def _draft_answer(
        self,
        question: str,
        *,
        source: str,
        listen_ms: int = 0,
        stt_ms: int = 0,
        stt_reused: bool = False,
        user_content: str | None = None,
        mode_override: str | None = None,
        replace_last: bool = False,
    ) -> None:
        if self._clients is None:
            return
        mode, kind = self._turn_mode(question, source=source, override=mode_override)
        preference = self._current_answer_mode()
        await self._emit(
            {
                "type": "question",
                "text": question,
                "valid": True,
                "source": source,
                "answer_mode": preference,
                "turn_mode": mode,
                "question_kind": kind,
                "replace": replace_last,
            }
        )
        await self._emit(
            {
                "type": "status",
                "state": "thinking",
                "detail": "Drafting typed question" if source == "typed" else "Drafting answer",
            }
        )

        llm_first_ms = 0
        llm_started = time.perf_counter()

        async def on_answer(text: str) -> None:
            nonlocal llm_first_ms
            if not llm_first_ms:
                llm_first_ms = ms_between(llm_started, time.perf_counter())
            await self._emit(
                {
                    "type": "answer_delta",
                    "text": text,
                    "source": source,
                    "answer_mode": preference,
                    "turn_mode": mode,
                    "question_kind": kind,
                }
            )

        try:
            turn = await copilot_turn(
                self._clients.chat,
                user_content or question,
                on_answer=on_answer,
                history=self._interview_history(exclude_last=replace_last),
                system_prompt=self._copilot_prompt(source=source, mode=mode),
                max_tokens=max_tokens_for_mode(mode, source=source),
            )
        except Exception:
            logger.exception("llm turn failed")
            await self._emit({"type": "error", "message": "The LLM could not draft a reply."})
            await self._emit({"type": "status", "state": "ready", "detail": "Paused"})
            return
        llm_ms = ms_between(llm_started, time.perf_counter())
        turn.question = question
        await self._publish(
            turn,
            question,
            listen_ms=listen_ms,
            stt_ms=stt_ms,
            llm_ms=llm_ms,
            llm_first_ms=llm_first_ms or llm_ms,
            stt_reused=stt_reused,
            streamed_question=True,
            source=source,
            turn_mode=mode,
            question_kind=kind,
            replace_last=replace_last,
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
        source: str = "spoken",
        turn_mode: str | None = None,
        question_kind: str | None = None,
        replace_last: bool = False,
    ) -> None:
        text = (transcript or turn.question or turn.latest_speech or "").strip()
        answer = (turn.answer or "").strip()
        if not text:
            await self._emit({"type": "status", "state": "ready", "detail": "Paused"})
            return
        if getattr(turn, "stage", "") == "not_a_question":
            await self._emit_skip(
                ClipVerdict(
                    action="skip",
                    reason="not_a_question",
                    text=text,
                    detail=DETAILS["not_a_question"],
                    retryable=True,
                ),
                source=source,
            )
            return

        preference = self._current_answer_mode()
        mode = normalize_answer_mode(turn_mode) if turn_mode else preference
        kind = question_kind or ""
        points = extract_talking_points(answer).as_dict() if answer else None
        payload = {
            "source": source,
            "answer_mode": preference,
            "turn_mode": mode,
            "question_kind": kind,
            "replace": replace_last,
        }
        if not streamed_question:
            await self._emit(
                {
                    "type": "question",
                    "text": text,
                    "valid": True,
                    **payload,
                }
            )
        if answer:
            await self._emit(
                {
                    "type": "answer",
                    "text": answer,
                    "talking_points": points,
                    **payload,
                }
            )
            await self._emit(
                {
                    "type": "qa",
                    "question": text,
                    "answer": answer,
                    "valid": True,
                    "talking_points": points,
                    **payload,
                }
            )
            logger.info("answer=%s", answer[:180])
        else:
            await self._emit(
                {
                    "type": "qa",
                    "question": text,
                    "answer": "(no answer drafted)",
                    "valid": True,
                    **payload,
                }
            )

        next_index = 1
        if self._session is not None:
            if replace_last and self._session.turns:
                next_index = self._session.turns[-1].index
            else:
                next_index = len(self._session.turns) + 1
        record = TurnRecord(
            index=next_index,
            question=text,
            answer=answer or "(no answer drafted)",
            listen_ms=listen_ms,
            stt_ms=stt_ms,
            llm_ms=llm_ms,
            llm_first_ms=llm_first_ms,
            total_ms=stt_ms + llm_ms,
            stt_reused=stt_reused,
            source=source,
            answer_mode=mode,
        )
        if self._session is not None:
            if replace_last and self._session.turns:
                self._session.turns[-1] = record
                self._session.updated_at = utc_now()
            else:
                self._session.add_turn(record)
            self._persist()
            await self._emit(
                {
                    "type": "turn_stats",
                    **record.as_dict(),
                    "session": self._session.snapshot(self._elapsed_ms()),
                }
            )
        await self._emit(
            {"type": "status", "state": "ready", "detail": "Paused · listen for the next question"}
        )

    async def _finish_session(self, *, emit_summary: bool = True) -> None:
        session = self._session
        if session is None or not session.active:
            return
        session.active = False
        session.ended_at = utc_now()
        duration = self._elapsed_ms()
        session.duration_ms = duration
        self._persist()
        if emit_summary:
            await self._emit(
                {
                    "type": "session_summary",
                    "started_at": session.started_at,
                    "ended_at": session.ended_at,
                    "session": session.snapshot(duration),
                }
            )
        self._forget_session()
