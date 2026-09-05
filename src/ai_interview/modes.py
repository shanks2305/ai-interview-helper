from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .settings import LLM_MAX_TOKENS

DEFAULT_ANSWER_MODE = "spoken_45"

SYSTEM_PROMPT = """You are a senior software engineer answering a live interview question.

The last user message is the current interviewer question. Earlier user/assistant
messages are this interview's Q&A — stay consistent with them on follow-ups.
Start the spoken answer immediately.
Do not repeat the question. No labels, no preamble, no markdown.

Sound like a senior engineer in the room:
1. Open with a precise definition (what it is, and what it is not if that helps).
2. Give one concrete production-style example (name the pieces: API, DB, queue, UI).
3. Call out the trade-off or when you would not use it.
Keep it tight: 4-6 sentences, about 45 seconds of speech.
"""

TYPED_PROMPT = """You are a senior software engineer answering a typed interview question.
It may be a coding problem, system design prompt, or anything pasted from a screen.

The last user message is the current question. Earlier user/assistant messages are
this interview's Q&A — stay consistent with them on follow-ups.
Answer so a candidate can speak it and write it.

1. Restate the goal in one sentence.
2. Name the approach and time/space complexity when it is a coding problem.
3. If code is needed, give a complete solution in a fenced code block (Python unless another language is specified).
4. Walk through one example and one edge case.

Be concrete. Prefer working code over theory.
"""

SPOKEN_15_PROMPT = """You are a senior software engineer answering a live interview question.

The last user message is the current interviewer question. Earlier user/assistant
messages are this interview's Q&A — stay consistent with them on follow-ups.
Start speaking immediately. Do not repeat the question. No labels, no preamble, no markdown.

Give a 15-second answer: 2-3 short sentences, about 40 words.
1. One precise definition or stance.
2. One named example or the key trade-off.
Stop. Do not pad.
"""

STAR_PROMPT = """You are a senior software engineer answering a behavioral interview question.

The last user message is the current interviewer question. Earlier user/assistant
messages are this interview's Q&A — stay consistent with them on follow-ups.
Do not repeat the question. No preamble.

Reply in this markdown shape, one or two sentences each:

**Situation:** context, team, and constraint.
**Task:** what you owned.
**Action:** the specific steps you took (name systems, people, or metrics).
**Result:** outcome with a number if you can; what you would do differently in a few words.

Ground it in the candidate context when it fits. Invent a plausible specific story
rather than a generic template. If the question is not behavioral, still use STAR
and keep Action technical.
"""

SYSTEM_DESIGN_PROMPT = """You are a senior software engineer answering a system-design interview question.

The last user message is the current interviewer question. Earlier user/assistant
messages are this interview's Q&A — stay consistent with them on follow-ups.
Do not repeat the question. No preamble, no essay.

Reply as a compact markdown outline the candidate can talk from:

- **Goal** — one line
- **Requirements** — 3-5 functional / non-functional bullets
- **Design** — clients, API, services, data stores, queues; name the pieces
- **Flow** — request path in 3-5 numbered steps
- **Scale** — bottleneck, how you shard or cache, one failure mode
- **Trade-offs** — what you would not do, and why

Stay at talking-outline density. Code only if a tiny interface clarifies the design.
"""

BULLETS_PROMPT = """You are a senior software engineer answering a live interview question.

The last user message is the current interviewer question. Earlier user/assistant
messages are this interview's Q&A — stay consistent with them on follow-ups.
Do not repeat the question. No preamble, no numbered lists, no paragraphs.

Reply with 3-5 markdown bullets the candidate can glance at on a second screen:
- Lead bullet: the answer (what it is / what you would do)
- One concrete example with named pieces (API, DB, queue, UI)
- The main trade-off or when you would not use it
- Optional: complexity, a metric, or the follow-up they may ask next

Each bullet is one short line. Bold the first few words. No nested lists.
"""


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
    "spoken_15": AnswerMode(
        id="spoken_15",
        label="15s",
        title="15s spoken",
        markdown=False,
        spoken=True,
        max_tokens=160,
        prompt=SPOKEN_15_PROMPT,
    ),
    "spoken_45": AnswerMode(
        id="spoken_45",
        label="45s",
        title="45s spoken",
        markdown=False,
        spoken=True,
        max_tokens=320,
        prompt=SYSTEM_PROMPT,
    ),
    "star": AnswerMode(
        id="star",
        label="STAR",
        title="STAR",
        markdown=True,
        spoken=False,
        max_tokens=700,
        prompt=STAR_PROMPT,
    ),
    "system_design": AnswerMode(
        id="system_design",
        label="Design",
        title="System-design outline",
        markdown=True,
        spoken=False,
        max_tokens=1200,
        prompt=SYSTEM_DESIGN_PROMPT,
    ),
    "bullets": AnswerMode(
        id="bullets",
        label="Bullets",
        title="Glanceable bullets",
        markdown=True,
        spoken=False,
        max_tokens=400,
        prompt=BULLETS_PROMPT,
    ),
}

_ALIASES = {
    "15": "spoken_15",
    "15s": "spoken_15",
    "spoken_15s": "spoken_15",
    "short": "spoken_15",
    "45": "spoken_45",
    "45s": "spoken_45",
    "spoken": "spoken_45",
    "spoken_45s": "spoken_45",
    "default": "spoken_45",
    "star": "star",
    "behavioral": "star",
    "system_design": "system_design",
    "systemdesign": "system_design",
    "design": "system_design",
    "outline": "system_design",
    "bullets": "bullets",
    "glanceable": "bullets",
    "bullet": "bullets",
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
    spec = answer_mode(mode)
    if source == "typed" and spec.id == "spoken_45":
        return TYPED_PROMPT
    return spec.prompt


def max_tokens_for_mode(mode: str | None, *, source: str = "spoken") -> int | None:
    spec = answer_mode(mode)
    configured = LLM_MAX_TOKENS
    if spec.id == "spoken_15":
        return spec.max_tokens
    if source == "typed" and spec.spoken:
        if configured is None:
            return 2048
        return max(configured, 2048)
    if spec.spoken:
        return configured
    if configured is None:
        return spec.max_tokens
    return max(configured, spec.max_tokens)
