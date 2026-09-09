from __future__ import annotations

import asyncio
from typing import Any

from fastapi import WebSocket

from .modes import DEFAULT_ANSWER_MODE, answer_mode_catalog, normalize_answer_mode
from .session import InterviewSession, empty_context, normalize_context
from .store import get_store
from .talking_points import extract_talking_points


class LiveHub:
    def __init__(self) -> None:
        self._viewers: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self.listening = False
        self.question = ""
        self.answer = ""
        self.source = ""
        self.pairs: list[dict[str, str]] = []
        self.session: dict[str, Any] | None = None
        self.context: dict[str, str] = empty_context()
        self.answer_mode = DEFAULT_ANSWER_MODE
        self.turn_mode = DEFAULT_ANSWER_MODE
        self.question_kind = ""
        self.talking_points: dict[str, Any] | None = None
        self._pipeline: Any = None
        self._standalone: Any = None
        self._drafting = False

    def snapshot(self) -> dict[str, Any]:
        return {
            "type": "snapshot",
            "listening": self.listening,
            "question": self.question,
            "answer": self.answer,
            "source": self.source,
            "pairs": list(self.pairs),
            "session": self.session,
            "context": dict(self.context),
            "answer_mode": self.answer_mode,
            "turn_mode": self.turn_mode,
            "question_kind": self.question_kind,
            "answer_modes": answer_mode_catalog(),
            "talking_points": dict(self.talking_points) if self.talking_points else None,
        }

    async def subscribe(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._viewers.add(websocket)
        await websocket.send_json(self.snapshot())

    async def unsubscribe(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._viewers.discard(websocket)

    def apply(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "status":
            state = event.get("state")
            if state != "thinking":
                self.listening = state == "listening"
            return
        if kind == "partial_question":
            self.question = event.get("text") or self.question
            self.answer = ""
            self.talking_points = None
            self._drafting = False
            if event.get("source"):
                self.source = event.get("source")
            return
        if kind == "question":
            self.question = event.get("text") or ""
            self.answer = ""
            self.talking_points = None
            self._drafting = False
            self.source = event.get("source") or "spoken"
            self._set_turn_mode(event)
            return
        if kind in {"answer", "answer_delta"}:
            self.answer = event.get("text") or ""
            self._drafting = kind == "answer_delta"
            if event.get("source"):
                self.source = event.get("source")
            self._set_turn_mode(event)
            self._refresh_talking_points(event.get("talking_points"), self.answer)
            return
        if kind == "qa":
            self._drafting = False
            self.question = event.get("question") or ""
            self.answer = event.get("answer") or ""
            self.source = event.get("source") or self.source or "spoken"
            self._set_turn_mode(event)
            self._refresh_talking_points(event.get("talking_points"), self.answer)
            pair = {
                "question": self.question,
                "answer": self.answer or "(no answer)",
                "source": self.source,
                "answer_mode": str(event.get("turn_mode") or self.turn_mode or self.answer_mode),
            }
            if event.get("replace") and self.pairs:
                self.pairs[0] = pair
            elif not self.pairs or self.pairs[0] != pair:
                self.pairs.insert(0, pair)
                self.pairs = self.pairs[:40]
            return
        if kind == "skip":
            if self._drafting or not self.answer:
                self.question = event.get("text") or self.question
                self.answer = event.get("detail") or self.answer
                self.talking_points = None
                self._drafting = False
                self.turn_mode = self.answer_mode
                self.question_kind = ""
                if event.get("source"):
                    self.source = event.get("source")
            return
        if kind in {"session_start", "session_resume", "turn_stats", "session_summary"}:
            self.session = event.get("session") or self.session
            self._sync_context(self.session)
            self._sync_mode(self.session)
            if kind == "session_start":
                self.pairs = []
                self.question = ""
                self.answer = ""
                self.source = ""
                self.talking_points = None
                self._drafting = False
                self.turn_mode = self.answer_mode
                self.question_kind = ""
            elif kind == "session_resume":
                self._apply_session_view(self.session)
            return
        if kind == "session_deleted":
            current_id = (self.session or {}).get("id")
            if current_id and current_id == event.get("session_id"):
                replacement = event.get("session")
                if replacement:
                    self.restore(replacement)
                else:
                    self.session = None
                    self.pairs = []
                    self.question = ""
                    self.answer = ""
                    self.source = ""
                    self.talking_points = None
                    self._drafting = False
                    self.turn_mode = self.answer_mode
                    self.question_kind = ""
            return
        if kind == "context_updated":
            if event.get("session"):
                self.session = event.get("session")
            self.context = normalize_context(event.get("context") or self.session)
            return
        if kind == "answer_mode_updated":
            if event.get("session"):
                self.session = event.get("session")
            self._set_mode(event.get("answer_mode"))
            self._sync_mode(self.session)
            return

    def restore(self, session: InterviewSession | dict[str, Any] | None) -> None:
        if session is None:
            return
        payload = session.snapshot() if isinstance(session, InterviewSession) else dict(session)
        self.session = payload
        self._sync_context(payload)
        self._sync_mode(payload)
        self._apply_session_view(payload)

    def _sync_context(self, session: dict[str, Any] | None) -> None:
        if not session:
            return
        if "context" not in session and not any(
            str(session.get(key) or "").strip()
            for key in ("role", "company", "job_description", "resume")
        ):
            return
        self.context = normalize_context(session)

    def _set_mode(self, value: Any) -> None:
        if value:
            self.answer_mode = normalize_answer_mode(str(value))

    def _set_turn_mode(self, event: dict[str, Any]) -> None:
        kind = event.get("question_kind")
        if kind:
            self.question_kind = str(kind)
        turn_mode = event.get("turn_mode") or event.get("answer_mode")
        if turn_mode:
            self.turn_mode = normalize_answer_mode(str(turn_mode))

    def _sync_mode(self, session: dict[str, Any] | None) -> None:
        if not session:
            return
        mode = session.get("answer_mode")
        if mode:
            self.answer_mode = normalize_answer_mode(str(mode))

    def _refresh_talking_points(self, payload: Any, answer: str) -> None:
        if isinstance(payload, dict) and payload.get("bullets"):
            bullets = [str(item).strip() for item in payload.get("bullets") or [] if str(item).strip()]
            self.talking_points = {
                "bullets": bullets[:5],
                "example": str(payload.get("example") or "").strip(),
            }
            return
        text = (answer or "").strip()
        if not text or text.startswith("Skipped") or text.startswith("Already answered"):
            self.talking_points = None
            return
        points = extract_talking_points(text)
        self.talking_points = points.as_dict() if points.bullets else None

    def _apply_session_view(self, session: dict[str, Any] | None) -> None:
        turns = (session or {}).get("turns") or []
        pairs: list[dict[str, str]] = []
        for turn in reversed(turns[-40:]):
            if not isinstance(turn, dict):
                continue
            pairs.append(
                {
                    "question": str(turn.get("question") or ""),
                    "answer": str(turn.get("answer") or ""),
                    "source": str(turn.get("source") or "spoken"),
                    "answer_mode": normalize_answer_mode(str(turn.get("answer_mode") or "")),
                }
            )
        self.pairs = pairs
        if turns and isinstance(turns[-1], dict):
            last = turns[-1]
            self.question = str(last.get("question") or "")
            self.answer = str(last.get("answer") or "")
            self.source = str(last.get("source") or "spoken")
            self.turn_mode = normalize_answer_mode(
                str(last.get("answer_mode") or (session or {}).get("answer_mode") or "")
            )
            self._refresh_talking_points(None, self.answer)
            return
        self.question = ""
        self.answer = ""
        self.source = ""
        self.talking_points = None
        self.turn_mode = self.answer_mode
        self.question_kind = ""

    async def publish(self, event: dict[str, Any]) -> None:
        self.apply(event)
        async with self._lock:
            viewers = list(self._viewers)
        stale: list[WebSocket] = []
        for websocket in viewers:
            try:
                await websocket.send_json(event)
            except Exception:
                stale.append(websocket)
        if stale:
            async with self._lock:
                for websocket in stale:
                    self._viewers.discard(websocket)

    def attach_pipeline(self, pipeline: Any) -> None:
        self._pipeline = pipeline
        seed = getattr(pipeline, "seed_pending", None)
        if callable(seed):
            seed(context=dict(self.context), answer_mode=self.answer_mode)

    def detach_pipeline(self, pipeline: Any) -> None:
        if self._pipeline is pipeline:
            self._pipeline = None
        standalone = self._standalone
        drop = getattr(standalone, "drop_stale_session", None) if standalone is not None else None
        if callable(drop):
            drop()

    def _active_pipeline(self) -> Any:
        pipeline = self._pipeline or self._standalone
        if pipeline is not None:
            return pipeline
        from .pipeline import InterviewPipeline

        self._standalone = InterviewPipeline(self.publish)
        return self._standalone

    async def mark_idle(self) -> None:
        await self.publish({"type": "status", "state": "idle", "detail": "Audio idle"})

    async def submit_ask(self, text: str) -> str | None:
        question = (text or "").strip()
        if not question:
            return "Paste a question first."
        await self._active_pipeline().ask_typed(question)
        return None

    async def submit_context(self, data: dict[str, Any] | None = None, **fields: Any) -> None:
        payload = normalize_context(data, **fields)
        await self._active_pipeline().set_context(payload)

    async def submit_answer_mode(self, mode: str | None) -> str:
        await self._active_pipeline().set_answer_mode(mode)
        return self.answer_mode

    async def submit_redraft(
        self,
        *,
        instruction: str | None = None,
        mode: str | None = None,
        question: str | None = None,
    ) -> str | None:
        text = (question or self.question or "").strip()
        if not text and not (self.session or {}).get("turns"):
            return "There is no question to rewrite yet."
        await self._active_pipeline().redraft(
            instruction=instruction,
            mode=mode,
            question=(question or self.question or None),
        )
        return None

    async def submit_revise(self, text: str) -> str | None:
        question = (text or "").strip()
        if not question:
            return "Edit the question first."
        await self._active_pipeline().revise_question(question)
        return None

    async def start_new_session(self) -> None:
        await self._active_pipeline().start_new_session()

    async def end_session(self) -> None:
        await self._active_pipeline().end_session()

    def _clear_session_view(self) -> None:
        self.session = None
        self.pairs = []
        self.question = ""
        self.answer = ""
        self.source = ""
        self.talking_points = None
        self._drafting = False
        self.turn_mode = self.answer_mode
        self.question_kind = ""

    async def delete_session(self, session_id: str) -> bool:
        sid = (session_id or "").strip()
        if not sid:
            return False
        pipeline = self._pipeline or self._standalone
        if pipeline is not None:
            deleted = pipeline.delete_stored_session(sid)
            store = pipeline._store()
        else:
            store = get_store()
            deleted = store.delete(sid)
        if not deleted:
            return False
        current_id = (self.session or {}).get("id")
        if current_id == sid:
            latest = store.latest()
            if latest is not None:
                self.restore(latest)
            else:
                self._clear_session_view()
        await self.publish({"type": "session_deleted", "session_id": sid, "session": self.session})
        return True


hub = LiveHub()
