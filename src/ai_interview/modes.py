from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .settings import LLM_MAX_TOKENS

DEFAULT_ANSWER_MODE = "spoken_45"

SYSTEM_PROMPT = """You are a senior software engineer writing a structured interview answer
the candidate can read on a second screen and speak from.

The last user message is the current question. Earlier turns are this interview.
On follow-ups, extend the same answer — do not restart.

Always use this markdown shape (skip a section only if it truly does not apply):

**Definition:** one or two precise sentences. What it is, and what it is not if that helps.

**Explanation:** how it works and why you'd use it. Be concrete: named systems
(API, Postgres, Redis, Kafka), complexity, trade-offs, what breaks in production.
Write as a senior: commit to one approach, mention the alternative in a clause.

**Example:** one production-style walkthrough with named pieces. For behavioral
questions, this is the story (what you owned, what you did, the result).

```language
complete code when the question needs it — algorithms, APIs, SQL, configs.
Python unless they named another language. No placeholders or "// ..." guts.
```

Omit the code fence when code would not help (pure behavioral, high-level design).
For coding problems the code block is required.

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
