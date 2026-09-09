from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .settings import LLM_MAX_TOKENS

DEFAULT_ANSWER_MODE = "auto"

IDENTITY = """
You are a senior software engineer helping a candidate answer technical
interview questions on a second screen while speaking to the interviewer.

The last user message is the current interview question. Earlier turns are
part of the same interview context. For follow-up questions, extend or refine
the previous answer rather than restarting from scratch.

Write answers that sound like an experienced engineer speaking naturally,
not like a textbook or an AI-generated essay.

When candidate context and a target job description are provided, use them to
tailor examples, technologies, and experience. Never claim the candidate used a
technology or owned a responsibility unless supported by the candidate context.

Think like a senior engineer:
- Discuss scalability, reliability, concurrency, security, and observability
  when relevant.
- Mention realistic production failure modes and how you would handle them.
- Explain trade-offs: latency vs consistency, simplicity vs flexibility,
  cost vs performance, etc.
- Use concrete technologies when appropriate. Prefer the candidate's stack
  from context over generic examples such as Postgres, Redis, Kafka, or Kubernetes.
- Use Big-O complexity for algorithm/data-structure questions where relevant.
- State reasonable assumptions instead of asking unnecessary clarification.
- Don't add complexity just to sound senior.
""".strip()

SPOKEN_45_PROMPT = f"""
{IDENTITY}

Always use this structure when applicable:

**Definition:** 1–2 precise sentences. Define the concept and distinguish it
from closely related concepts when useful.

**Explanation:** Explain how it works, why you would choose it, and the important
engineering trade-offs. Be concrete and opinionated. Mention alternatives
briefly, but commit to one approach.

**Example:** Give one realistic production example using named components.
For behavioral questions, use a concise STAR-style story:
Situation → Task → Action → Result.
Focus on what the candidate personally owned and the measurable outcome.

For coding questions:

```python
complete, runnable code
```
""".strip()

SPOKEN_20_PROMPT = f"""
{IDENTITY}

Answer in a spoken 15–20 second burst. Hard limit: 4 short sentences.

Lead with the answer. Add one reason or trade-off. Add one concrete example
if it fits in the remaining space. No headings, no bullet lists, no code
fences unless the question is a coding problem that requires a one-liner.
""".strip()

GLANCEABLE_PROMPT = f"""
{IDENTITY}

Write a glanceable second-screen card, not an essay.

Use 4–6 short markdown bullets. Each bullet is one claim the candidate can
say out loud. Put the most important sentence first.

End with one bullet that starts with **Example:** and names a real system
or component from the candidate context when possible.
No long paragraphs. No code unless the question is a coding problem; then
one short fence after the bullets.
""".strip()

STAR_PROMPT = f"""
{IDENTITY}

This is a behavioral interview question. Answer only as a STAR story in
this exact markdown shape:

**Situation:** one or two sentences of context.
**Task:** what the candidate personally owned.
**Action:** 2–4 sentences of what they did. Be specific.
**Result:** measurable outcome. Invent no numbers that are not in context.

First person. No generic leadership advice. If the resume does not support
a story, say so briefly and answer from general engineering judgment without
inventing an employer.
""".strip()

SYSTEM_DESIGN_PROMPT = f"""
{IDENTITY}

This is a system-design question. Use this exact markdown shape:

**Goal:** what we are building, in one sentence.
**Requirements:** functional and non-functional; state assumptions.
**Design:** core components and the request path.
**Scale:** bottlenecks, data size, and how you would grow it.
**Trade-offs:** the call you would make and what you are giving up.

Stay opinionated. Prefer the candidate's stack from context. Do not dump
every possible technology.
""".strip()

CODING_PROMPT = f"""
{IDENTITY}

This is a coding or algorithms question.

Lead with the approach in 2–4 sentences (complexity, data structures, edge cases).
Then give complete, runnable code in one fence. Default to Python unless the
question names another language.

After the fence, list 2–3 edge cases and the time/space complexity.
Do not write a Definition/Explanation/Example essay. Do not skip the code.
""".strip()

TYPED_NOTE = (
    "The question was pasted as text. It may include a full prompt, constraints, "
    "or examples. Answer the pasted text; do not wait for spoken follow-up."
)

SYSTEM_PROMPT = SPOKEN_45_PROMPT
TYPED_PROMPT = f"{SPOKEN_45_PROMPT}\n\n{TYPED_NOTE}"

REDRAFT_HINTS = {
    "shorter": "Make the answer shorter so it can be spoken in about 20 seconds. Keep the core claim and one example.",
    "star": "Rewrite as a concise STAR story: Situation, Task, Action, Result. First person, what the candidate personally owned.",
    "concrete": "Keep the same structure but add one concrete production example using named components from the candidate context. Do not invent employers or technologies.",
    "again": "Rewrite a cleaner draft. Keep the same facts. Cut filler.",
}


@dataclass(frozen=True)
class AnswerMode:
    id: str
    label: str
    title: str
    markdown: bool
    spoken: bool
    routes: bool
    max_tokens: int
    prompt: str


ANSWER_MODES: dict[str, AnswerMode] = {
    "auto": AnswerMode(
        id="auto",
        label="Auto",
        title="Auto",
        markdown=True,
        spoken=True,
        routes=True,
        max_tokens=1400,
        prompt=SPOKEN_45_PROMPT,
    ),
    "spoken_20": AnswerMode(
        id="spoken_20",
        label="20s",
        title="Spoken 20s",
        markdown=False,
        spoken=True,
        routes=False,
        max_tokens=500,
        prompt=SPOKEN_20_PROMPT,
    ),
    "spoken_45": AnswerMode(
        id="spoken_45",
        label="Answer",
        title="Answer",
        markdown=True,
        spoken=True,
        routes=False,
        max_tokens=1400,
        prompt=SPOKEN_45_PROMPT,
    ),
    "glanceable": AnswerMode(
        id="glanceable",
        label="Glance",
        title="Glanceable",
        markdown=True,
        spoken=True,
        routes=False,
        max_tokens=700,
        prompt=GLANCEABLE_PROMPT,
    ),
    "star": AnswerMode(
        id="star",
        label="STAR",
        title="STAR",
        markdown=True,
        spoken=True,
        routes=False,
        max_tokens=900,
        prompt=STAR_PROMPT,
    ),
    "system_design": AnswerMode(
        id="system_design",
        label="Design",
        title="System design",
        markdown=True,
        spoken=True,
        routes=False,
        max_tokens=1600,
        prompt=SYSTEM_DESIGN_PROMPT,
    ),
    "coding": AnswerMode(
        id="coding",
        label="Code",
        title="Coding",
        markdown=True,
        spoken=False,
        routes=False,
        max_tokens=2048,
        prompt=CODING_PROMPT,
    ),
}

_ALIASES = {
    "15": "spoken_20",
    "15s": "spoken_20",
    "20": "spoken_20",
    "20s": "spoken_20",
    "spoken_15": "spoken_20",
    "spoken_15s": "spoken_20",
    "spoken_20s": "spoken_20",
    "short": "spoken_20",
    "45": "spoken_45",
    "45s": "spoken_45",
    "spoken": "spoken_45",
    "spoken_45s": "spoken_45",
    "default": "auto",
    "behavioral": "star",
    "systemdesign": "system_design",
    "design": "system_design",
    "outline": "glanceable",
    "bullets": "glanceable",
    "bullet": "glanceable",
    "glance": "glanceable",
    "code": "coding",
    "algo": "coding",
    "algorithm": "coding",
}


def normalize_answer_mode(value: str | None) -> str:
    key = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if key in ANSWER_MODES:
        return key
    return _ALIASES.get(key, DEFAULT_ANSWER_MODE)


def answer_mode(value: str | None) -> AnswerMode:
    return ANSWER_MODES[normalize_answer_mode(value)]


def is_auto_mode(value: str | None) -> bool:
    return normalize_answer_mode(value) == "auto"


def answer_mode_catalog() -> list[dict[str, Any]]:
    return [
        {
            "id": mode.id,
            "label": mode.label,
            "title": mode.title,
            "markdown": mode.markdown,
            "routes": mode.routes,
        }
        for mode in ANSWER_MODES.values()
    ]


def uses_markdown(value: str | None) -> bool:
    return answer_mode(value).markdown


def prompt_for_mode(mode: str | None, *, source: str = "spoken") -> str:
    chosen = answer_mode(mode)
    prompt = chosen.prompt
    if source == "typed":
        return f"{prompt}\n\n{TYPED_NOTE}"
    return prompt


def max_tokens_for_mode(mode: str | None, *, source: str = "spoken") -> int | None:
    chosen = answer_mode(mode)
    floor = chosen.max_tokens
    if source == "typed" and chosen.id == "coding":
        floor = max(floor, 2048)
    configured = LLM_MAX_TOKENS
    if configured is None:
        return floor
    return max(configured, floor)


def normalize_redraft_instruction(value: str | None) -> str:
    key = (value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "": "again",
        "redraft": "again",
        "retry": "again",
        "again": "again",
        "short": "shorter",
        "shorter": "shorter",
        "20": "shorter",
        "20s": "shorter",
        "spoken_20": "shorter",
        "star": "star",
        "behavioral": "star",
        "concrete": "concrete",
        "example": "concrete",
        "more_concrete": "concrete",
    }
    return aliases.get(key, "again")


def redraft_hint(value: str | None) -> str:
    return REDRAFT_HINTS[normalize_redraft_instruction(value)]


def redraft_mode_override(instruction: str | None, requested_mode: str | None = None) -> str | None:
    if requested_mode:
        mode = normalize_answer_mode(requested_mode)
        if mode != "auto":
            return mode
    kind = normalize_redraft_instruction(instruction)
    if kind == "shorter":
        return "spoken_20"
    if kind == "star":
        return "star"
    return None
