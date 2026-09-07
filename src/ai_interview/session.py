from __future__ import annotations

import html
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .modes import DEFAULT_ANSWER_MODE, normalize_answer_mode

MEMORY_TURNS = 8
MEMORY_ANSWER_CHARS = 1500
STALE_AFTER_SECONDS = 8 * 3600
CONTEXT_KEYS = ("role", "company", "job_description", "resume")


def empty_context() -> dict[str, str]:
    return {key: "" for key in CONTEXT_KEYS}


def normalize_context(data: dict[str, Any] | None = None, **fields: Any) -> dict[str, str]:
    source = {**(data or {}), **fields}
    nested = source.get("context")
    if isinstance(nested, dict):
        source = {**nested, **source}
    return {key: str(source.get(key) or "").strip() for key in CONTEXT_KEYS}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def ms_between(started: float, ended: float) -> int:
    return max(0, int((ended - started) * 1000))


def format_ms(ms: int) -> str:
    value = max(0, int(ms or 0))
    if value < 1000:
        return f"{value}ms"
    return f"{value / 1000:.1f}s"


def format_elapsed(ms: int) -> str:
    total = max(0, int(ms or 0) // 1000)
    minutes, seconds = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _parse_iso(value: str | None) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


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
    source: str = "spoken"
    answer_mode: str = DEFAULT_ANSWER_MODE

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["answer_mode"] = normalize_answer_mode(self.answer_mode)
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TurnRecord:
        return cls(
            index=int(data.get("index") or 0),
            question=str(data.get("question") or ""),
            answer=str(data.get("answer") or ""),
            listen_ms=int(data.get("listen_ms") or 0),
            stt_ms=int(data.get("stt_ms") or 0),
            llm_ms=int(data.get("llm_ms") or 0),
            llm_first_ms=int(data.get("llm_first_ms") or 0),
            total_ms=int(data.get("total_ms") or 0),
            stt_reused=bool(data.get("stt_reused")),
            source=str(data.get("source") or "spoken"),
            answer_mode=normalize_answer_mode(str(data.get("answer_mode") or "")),
        )


@dataclass
class InterviewSession:
    id: str = field(default_factory=lambda: uuid4().hex)
    started_at: str = field(default_factory=utc_now)
    ended_at: str | None = None
    updated_at: str = field(default_factory=utc_now)
    turns: list[TurnRecord] = field(default_factory=list)
    active: bool = True
    duration_ms: int = 0
    role: str = ""
    company: str = ""
    job_description: str = ""
    resume: str = ""
    answer_mode: str = DEFAULT_ANSWER_MODE

    def add_turn(self, turn: TurnRecord) -> None:
        self.turns.append(turn)
        self.updated_at = utc_now()

    def context_payload(self) -> dict[str, str]:
        return {
            "role": self.role,
            "company": self.company,
            "job_description": self.job_description,
            "resume": self.resume,
        }

    def has_context(self) -> bool:
        return any(value.strip() for value in self.context_payload().values())

    def context_label(self) -> str:
        role = self.role.strip()
        company = self.company.strip()
        if role and company:
            return f"{role} · {company}"
        if role or company:
            return role or company
        if self.has_context():
            return "JD and resume saved"
        return ""

    def set_context(self, data: dict[str, Any] | None = None, **fields: Any) -> None:
        payload = normalize_context(data, **fields)
        self.role = payload["role"]
        self.company = payload["company"]
        self.job_description = payload["job_description"]
        self.resume = payload["resume"]
        self.updated_at = utc_now()

    def set_answer_mode(self, mode: str | None) -> str:
        self.answer_mode = normalize_answer_mode(mode)
        self.updated_at = utc_now()
        return self.answer_mode

    def copy_context_from(self, other: InterviewSession | None) -> None:
        if other is None:
            return
        self.set_context(other.context_payload())
        self.set_answer_mode(other.answer_mode)

    def title(self) -> str:
        for turn in self.turns:
            question = (turn.question or "").strip().split("\n", 1)[0]
            if question:
                if len(question) > 72:
                    return question[:71].rstrip() + "…"
                return question
        return "Empty session"

    def export_stem(self) -> str:
        day = (self.started_at or "")[:10] or "session"
        return f"interview-{day}-{self.id[:8]}"

    def is_stale(self, now: datetime | None = None) -> bool:
        stamp = _parse_iso(self.updated_at) or _parse_iso(self.started_at)
        if stamp is None:
            return False
        current = now or datetime.now(timezone.utc)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return (current - stamp).total_seconds() > STALE_AFTER_SECONDS

    def recent_qa(self, limit: int = MEMORY_TURNS) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        for turn in self.turns[-limit:]:
            question = (turn.question or "").strip()
            if not question:
                continue
            answer = (turn.answer or "").strip()
            if len(answer) > MEMORY_ANSWER_CHARS:
                answer = answer[: MEMORY_ANSWER_CHARS - 1].rstrip() + "…"
            pairs.append((question, answer))
        return pairs

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
        elapsed = self.duration_ms if duration_ms is None else duration_ms
        return {
            "id": self.id,
            "active": self.active,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "updated_at": self.updated_at,
            "title": self.title(),
            "turns": [turn.as_dict() for turn in self.turns],
            "stats": self.summary(elapsed),
            "context": self.context_payload(),
            "answer_mode": normalize_answer_mode(self.answer_mode),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InterviewSession:
        turns = [TurnRecord.from_dict(item) for item in data.get("turns") or [] if isinstance(item, dict)]
        stats = data.get("stats") if isinstance(data.get("stats"), dict) else {}
        duration = data.get("duration_ms")
        if duration is None:
            duration = stats.get("duration_ms") if isinstance(stats, dict) else 0
        context = normalize_context(data)
        return cls(
            id=str(data.get("id") or uuid4().hex),
            started_at=str(data.get("started_at") or utc_now()),
            ended_at=data.get("ended_at"),
            updated_at=str(data.get("updated_at") or data.get("started_at") or utc_now()),
            turns=turns,
            active=bool(data.get("active")),
            duration_ms=int(duration or 0),
            role=context["role"],
            company=context["company"],
            job_description=context["job_description"],
            resume=context["resume"],
            answer_mode=normalize_answer_mode(str(data.get("answer_mode") or "")),
        )


def session_markdown(session: InterviewSession) -> str:
    stats = session.summary(session.duration_ms)
    lines = [
        f"# {session.title()}",
        "",
        f"- Started: {session.started_at}",
        f"- Ended: {session.ended_at or 'in progress'}",
        f"- Questions: {stats['questions']}",
        f"- Duration: {format_elapsed(stats['duration_ms'])}",
    ]
    if session.role.strip():
        lines.append(f"- Role: {session.role.strip()}")
    if session.company.strip():
        lines.append(f"- Company: {session.company.strip()}")
    if stats["questions"]:
        lines.append(
            "- Timing: "
            f"avg listen {format_ms(stats['avg_listen_ms'])}, "
            f"avg answer {format_ms(stats['avg_answer_ms'])} "
            f"(fastest {format_ms(stats['fastest_answer_ms'])}, "
            f"slowest {format_ms(stats['slowest_answer_ms'])})"
        )
    lines.append("")
    if not session.turns:
        lines.append("No questions were recorded in this session.")
        lines.append("")
        return "\n".join(lines)
    for turn in session.turns:
        question = (turn.question or "").strip() or "(no question)"
        answer = (turn.answer or "").strip() or "(no answer)"
        source = turn.source or "spoken"
        lines.extend(
            [
                f"## {turn.index}. {question}",
                "",
                answer,
                "",
                (
                    f"_{source} · listen {format_ms(turn.listen_ms)} · "
                    f"transcribe {format_ms(turn.stt_ms)} · "
                    f"draft {format_ms(turn.llm_ms)}_"
                ),
                "",
            ]
        )
    return "\n".join(lines)


def session_print_html(session: InterviewSession, *, autoprint: bool = False) -> str:
    stats = session.summary(session.duration_ms)
    turns_html = []
    for turn in session.turns:
        question = html.escape((turn.question or "").strip() or "(no question)")
        answer = html.escape((turn.answer or "").strip() or "(no answer)")
        source = html.escape(turn.source or "spoken")
        turns_html.append(
            "<article class='turn'>"
            f"<h2>Q{html.escape(str(turn.index))}. {question}</h2>"
            f"<pre class='answer'>{answer}</pre>"
            f"<p class='meta'>{source} · listen {format_ms(turn.listen_ms)} · "
            f"transcribe {format_ms(turn.stt_ms)} · draft {format_ms(turn.llm_ms)}</p>"
            "</article>"
        )
    body = "".join(turns_html) or "<p class='empty'>No questions were recorded in this session.</p>"
    print_script = (
        "<script>window.addEventListener('load',()=>setTimeout(()=>window.print(),250));</script>"
        if autoprint
        else ""
    )
    title = html.escape(session.title())
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    :root {{ color-scheme: light; }}
    body {{
      margin: 0 auto;
      max-width: 760px;
      padding: 32px 24px 48px;
      font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: #12141a;
    }}
    h1 {{ font-size: 28px; letter-spacing: -0.03em; margin: 0 0 8px; }}
    .lede {{ color: #4b5568; margin: 0 0 24px; }}
    .toolbar {{ display: flex; gap: 8px; margin: 0 0 24px; }}
    .toolbar button {{
      font: inherit;
      padding: 8px 12px;
      border-radius: 10px;
      border: 1px solid #d1d5db;
      background: #111827;
      color: #fff;
      cursor: pointer;
    }}
    .turn {{ margin: 0 0 28px; break-inside: avoid; }}
    h2 {{ font-size: 18px; margin: 0 0 8px; }}
    .answer {{
      white-space: pre-wrap;
      font: inherit;
      margin: 0;
      padding: 12px 14px;
      background: #f8fafc;
      border: 1px solid #e5e7eb;
      border-radius: 12px;
    }}
    .meta, .empty {{ color: #6b7280; font-size: 13px; }}
    @media print {{
      .toolbar {{ display: none; }}
      body {{ padding: 0; }}
      a {{ color: inherit; text-decoration: none; }}
    }}
  </style>
</head>
<body>
  <div class="toolbar">
    <button type="button" onclick="window.print()">Print / Save as PDF</button>
  </div>
  <h1>{title}</h1>
  <p class="lede">
    {html.escape(str(stats["questions"]))} questions ·
    {html.escape(format_elapsed(stats["duration_ms"]))} ·
    started {html.escape(session.started_at)}
    {"" if not session.ended_at else " · ended " + html.escape(session.ended_at)}
    {"" if not session.context_label() else " · " + html.escape(session.context_label())}
  </p>
  {body}
  {print_script}
</body>
</html>
"""


def _avg(values: list[int]) -> int:
    if not values:
        return 0
    return int(round(sum(values) / len(values)))
