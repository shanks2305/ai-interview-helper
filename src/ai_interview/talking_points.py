from __future__ import annotations

import re
from dataclasses import dataclass

_CODE_FENCE = re.compile(r"```[\s\S]*?```")
_BULLET_LINE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.+)$")
_STAR_LINE = re.compile(
    r"^\s*(?:\*\*)?(Situation|Task|Action|Result|Goal|Requirements|Design|Flow|Scale|Trade-?offs?)\*?\*?\s*[:—\-]\s*(.+)$",
    re.I,
)
_BOLD_LEAD = re.compile(r"^\s*(?:(?:[-*+]|\d+[.)])\s+)?\*\*(.+?)\*\*\s*[—:\-]\s*(.+)$")
_HEADING = re.compile(r"^#{1,6}\s+")
_MARKUP = re.compile(r"[*_`#]+")
_EXAMPLE_HINT = re.compile(
    r"\b("
    r"for example|e\.g\.|such as|say we|say you|"
    r"in production|production|"
    r"postgres|postgresql|redis|kafka|stripe|dynamo|s3|"
    r"webhook|checkout|inventory|mutex|semaphore|queue|\bapi\b|\bdb\b"
    r")\b",
    re.I,
)


@dataclass(frozen=True)
class TalkingPoints:
    bullets: list[str]
    example: str = ""

    def as_dict(self) -> dict[str, object]:
        return {"bullets": list(self.bullets), "example": self.example}

    def as_text(self) -> str:
        lines = [f"• {item}" for item in self.bullets]
        if self.example:
            if lines:
                lines.append("")
            lines.append(f"Example: {self.example}")
        return "\n".join(lines)


def extract_talking_points(text: str) -> TalkingPoints:
    cleaned = _strip_code(text)
    if not cleaned:
        return TalkingPoints([])
    labeled = _from_star(cleaned) or _from_bullets(cleaned) or _from_sentences(cleaned)
    items = [_clean_item(item) for item in labeled]
    items = [item for item in items if item[0]]
    if not items:
        fallback = _clean_item((cleaned, ""))
        return TalkingPoints([fallback[0]] if fallback[0] else [])

    example = _pick_example(items)
    others = [text for text, _label in items if text != example]
    if example and len(others) >= 3:
        return TalkingPoints(bullets=others[:5], example=example)
    return TalkingPoints(bullets=[text for text, _label in items][:5], example="")


def _strip_code(text: str) -> str:
    stripped = _CODE_FENCE.sub("\n", text or "")
    return stripped.strip()


def _clean_item(item: tuple[str, str]) -> tuple[str, str]:
    text, label = item
    value = _MARKUP.sub("", (text or "").replace("\n", " "))
    value = re.sub(r"\s+", " ", value).strip().strip("-—: ")
    name = (label or "").strip()
    if name and value and not re.match(rf"^{re.escape(name)}\b", value, flags=re.I):
        value = f"{name}: {value}"
    if len(value) > 220:
        value = value[:219].rstrip() + "…"
    return value, name


def _from_star(text: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = _HEADING.sub("", raw).strip()
        match = _STAR_LINE.match(line)
        if not match:
            continue
        label = _canonical_label(match.group(1))
        found.append((match.group(2).strip(), label))
    return found if len(found) >= 2 else []


def _from_bullets(text: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = _HEADING.sub("", raw).strip()
        bold = _BOLD_LEAD.match(line)
        if bold:
            found.append((bold.group(2).strip(), _canonical_label(bold.group(1))))
            continue
        match = _BULLET_LINE.match(line)
        if match:
            found.append((match.group(1).strip(), ""))
    return found if len(found) >= 2 else []


def _from_sentences(text: str) -> list[tuple[str, str]]:
    collapsed = _HEADING.sub("", text)
    collapsed = re.sub(r"\s+", " ", collapsed).strip()
    parts = re.split(r"(?<=[.!?])\s+", collapsed)
    sentences = [part.strip() for part in parts if len(part.strip()) > 12]
    if not sentences and collapsed:
        sentences = [collapsed]
    return [(sentence, "") for sentence in sentences[:8]]


def _canonical_label(value: str) -> str:
    key = re.sub(r"[^a-z]+", "", (value or "").lower())
    names = {
        "situation": "Situation",
        "task": "Task",
        "action": "Action",
        "result": "Result",
        "goal": "Goal",
        "requirements": "Requirements",
        "design": "Design",
        "flow": "Flow",
        "scale": "Scale",
        "tradeoff": "Trade-off",
        "tradeoffs": "Trade-offs",
    }
    return names.get(key, (value or "").strip().title())


def _pick_example(items: list[tuple[str, str]]) -> str:
    scored: list[tuple[int, str]] = []
    for index, (text, label) in enumerate(items):
        score = 0
        if _EXAMPLE_HINT.search(text):
            score += 3
        if label.lower() in {"action", "design", "flow"}:
            score += 4
        if re.search(r"\b[A-Z][a-zA-Z]+\b", text) and index > 0:
            score += 1
        if len(text) > 90:
            score += 1
        if index == 0:
            score -= 1
        if score > 0:
            scored.append((score, text))
    if not scored:
        return ""
    scored.sort(key=lambda item: (-item[0], -len(item[1])))
    return scored[0][1]
