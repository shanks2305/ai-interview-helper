from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def ms_between(started: float, ended: float) -> int:
    return max(0, int((ended - started) * 1000))


@dataclass
class TurnRecord:
    index: int
    question: str
    answer: str
    listen_ms: int
    stt_ms: int
    llm_ms: int
    llm_first_ms: int
    total_ms: int
    stt_reused: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class InterviewSession:
    started_at: str = field(default_factory=utc_now)
    ended_at: str | None = None
    turns: list[TurnRecord] = field(default_factory=list)
    active: bool = True

    def add_turn(self, turn: TurnRecord) -> None:
        self.turns.append(turn)

    def summary(self, duration_ms: int) -> dict[str, Any]:
        turns = self.turns
        count = len(turns)
        listen = [item.listen_ms for item in turns]
        stt = [item.stt_ms for item in turns]
        llm = [item.llm_ms for item in turns]
        first = [item.llm_first_ms for item in turns if item.llm_first_ms]
        total = [item.total_ms for item in turns]
        return {
            "questions": count,
            "duration_ms": duration_ms,
            "avg_listen_ms": _avg(listen),
            "avg_stt_ms": _avg(stt),
            "avg_llm_ms": _avg(llm),
            "avg_llm_first_ms": _avg(first),
            "avg_answer_ms": _avg(total),
            "fastest_answer_ms": min(total) if total else 0,
            "slowest_answer_ms": max(total) if total else 0,
        }

    def snapshot(self, duration_ms: int | None = None) -> dict[str, Any]:
        elapsed = duration_ms if duration_ms is not None else 0
        payload: dict[str, Any] = {
            "active": self.active,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "turns": [turn.as_dict() for turn in self.turns],
            "stats": self.summary(elapsed),
        }
        return payload


def _avg(values: list[int]) -> int:
    if not values:
        return 0
    return int(round(sum(values) / len(values)))
