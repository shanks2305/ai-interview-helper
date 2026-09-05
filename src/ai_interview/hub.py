from __future__ import annotations

import asyncio
from typing import Any

from fastapi import WebSocket


class LiveHub:
    def __init__(self) -> None:
        self._viewers: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self.listening = False
        self.question = ""
        self.answer = ""
        self.pairs: list[dict[str, str]] = []
        self.session: dict[str, Any] | None = None
        self._pipeline: Any = None
        self._standalone: Any = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "type": "snapshot",
            "listening": self.listening,
            "question": self.question,
            "answer": self.answer,
            "pairs": list(self.pairs),
            "session": self.session,
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
            self.listening = state == "listening"
            return
        if kind == "partial_question":
            self.question = event.get("text") or self.question
            return
        if kind == "question":
            self.question = event.get("text") or ""
            self.answer = ""
            return
        if kind in {"answer", "answer_delta"}:
            self.answer = event.get("text") or ""
            return
        if kind == "qa":
            self.question = event.get("question") or ""
            self.answer = event.get("answer") or ""
            pair = {"question": self.question, "answer": self.answer or "(no answer)"}
            if not self.pairs or self.pairs[0] != pair:
                self.pairs.insert(0, pair)
                self.pairs = self.pairs[:40]
            return
        if kind in {"session_start", "turn_stats", "session_summary"}:
            self.session = event.get("session") or self.session
            if kind == "session_start":
                self.pairs = []
                self.question = ""
                self.answer = ""

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

    def detach_pipeline(self, pipeline: Any) -> None:
        if self._pipeline is pipeline:
            self._pipeline = None

    async def submit_ask(self, text: str) -> str | None:
        question = (text or "").strip()
        if not question:
            return "Paste a question first."
        pipeline = self._pipeline or self._standalone
        if pipeline is None:
            from .pipeline import InterviewPipeline

            self._standalone = InterviewPipeline(self.publish)
            pipeline = self._standalone
        await pipeline.ask_typed(question)
        return None


hub = LiveHub()
