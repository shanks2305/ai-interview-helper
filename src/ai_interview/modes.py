from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .settings import LLM_MAX_TOKENS

DEFAULT_ANSWER_MODE = "spoken_45"

SYSTEM_PROMPT = """
You are a senior software engineer helping a candidate answer technical
interview questions on a second screen while speaking to the interviewer.

The last user message is the current interview question. Earlier turns are
part of the same interview context. For follow-up questions, extend or refine
the previous answer rather than restarting from scratch.

Write answers that sound like an experienced engineer speaking naturally,
not like a textbook or an AI-generated essay.

Always use this structure when applicable:

**Definition:** 1–2 precise sentences. Define the concept and distinguish it
from closely related concepts when useful.

**Explanation:** Explain how it works, why you would choose it, and the important
engineering trade-offs. Be concrete and opinionated. Mention alternatives
briefly, but commit to one approach.

Think like a senior engineer:
- Discuss scalability, reliability, concurrency, security, and observability
  when relevant.
- Mention realistic production failure modes and how you would handle them.
- Explain trade-offs: latency vs consistency, simplicity vs flexibility,
  cost vs performance, etc.
- Use concrete technologies when appropriate: API, Postgres, Redis, Kafka,
  S3, Kubernetes, etc.
- Use Big-O complexity for algorithm/data-structure questions where relevant.
- State reasonable assumptions instead of asking unnecessary clarification.
- Don't add complexity just to sound senior.

**Example:** Give one realistic production example using named components.
For behavioral questions, use a concise STAR-style story:
Situation → Task → Action → Result.
Focus on what the candidate personally owned and the measurable outcome.

For coding questions:

```python
complete, runnable code

Rules:
- No "great question". Do not repeat the question.
- Use candidate context (resume/JD) when it fits; do not contradict it.
- Do not invent employers that contradict the resume.
- Keep it tight enough to talk from. No essay.
"""

TYPED_PROMPT = SYSTEM_PROMPT


@dataclass(frozen=True)
class AnswerMode:
    id: str
    label: str
    title: str
    markdown: bool
    spoken: bool
    max_tokens: int
    prompt: str


ANSWER_MODES: dict[str, AnswerMode] = {
    "spoken_45": AnswerMode(
        id="spoken_45",
        label="Answer",
        title="Answer",
        markdown=True,
        spoken=True,
        max_tokens=1400,
        prompt=SYSTEM_PROMPT,
    ),
}

_ALIASES = {
    "15": "spoken_45",
    "15s": "spoken_45",
    "spoken_15": "spoken_45",
    "spoken_15s": "spoken_45",
    "short": "spoken_45",
    "45": "spoken_45",
    "45s": "spoken_45",
    "spoken": "spoken_45",
    "spoken_45s": "spoken_45",
    "default": "spoken_45",
    "star": "spoken_45",
    "behavioral": "spoken_45",
    "system_design": "spoken_45",
    "systemdesign": "spoken_45",
    "design": "spoken_45",
    "outline": "spoken_45",
    "bullets": "spoken_45",
    "glanceable": "spoken_45",
    "bullet": "spoken_45",
}


def normalize_answer_mode(value: str | None) -> str:
    key = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if key in ANSWER_MODES:
        return key
    return _ALIASES.get(key, DEFAULT_ANSWER_MODE)


def answer_mode(value: str | None) -> AnswerMode:
    return ANSWER_MODES[normalize_answer_mode(value)]


def answer_mode_catalog() -> list[dict[str, Any]]:
    return [
        {
            "id": mode.id,
            "label": mode.label,
            "title": mode.title,
            "markdown": mode.markdown,
        }
        for mode in ANSWER_MODES.values()
    ]


def uses_markdown(value: str | None) -> bool:
    return answer_mode(value).markdown


def prompt_for_mode(mode: str | None, *, source: str = "spoken") -> str:
    if source == "typed":
        return TYPED_PROMPT
    return SYSTEM_PROMPT


def max_tokens_for_mode(mode: str | None, *, source: str = "spoken") -> int | None:
    configured = LLM_MAX_TOKENS
    floor = 2048 if source == "typed" else 1400
    if configured is None:
        return floor
    return max(configured, floor)
