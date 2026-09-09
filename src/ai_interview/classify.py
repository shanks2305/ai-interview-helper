from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

Action = Literal["answer", "skip"]
QuestionKind = Literal["coding", "system_design", "behavioral", "technical"]
Reason = Literal[
    "question",
    "follow_up",
    "duplicate",
    "filler",
    "greeting",
    "incomplete",
    "overlap",
    "not_a_question",
]

DETAILS: dict[str, str] = {
    "question": "Interview question",
    "follow_up": "Follow-up question",
    "duplicate": "Already answered",
    "filler": "Skipped · filler",
    "greeting": "Skipped · not a question",
    "incomplete": "Skipped · false start",
    "overlap": "Skipped · overlapping speech",
    "not_a_question": "Skipped · not a question",
}


@dataclass(frozen=True)
class ClipVerdict:
    action: Action
    reason: Reason
    text: str
    detail: str
    retryable: bool = True

    def as_event(self, *, source: str) -> dict[str, str | bool]:
        return {
            "type": "skip",
            "reason": self.reason,
            "text": self.text,
            "detail": self.detail,
            "retryable": self.retryable,
            "source": source,
        }


_CONTRACTIONS = (
    ("what's", "what is"),
    ("how's", "how is"),
    ("where's", "where is"),
    ("who's", "who is"),
    ("that's", "that is"),
    ("there's", "there is"),
    ("it's", "it is"),
    ("can't", "can not"),
    ("don't", "do not"),
    ("doesn't", "does not"),
    ("isn't", "is not"),
    ("aren't", "are not"),
    ("won't", "will not"),
    ("let's", "let us"),
    ("you're", "you are"),
    ("we're", "we are"),
    ("they're", "they are"),
    ("i'd", "i would"),
    ("you'd", "you would"),
    ("we'd", "we would"),
)

_FILLER_PHRASES = (
    "you know",
    "i mean",
    "kind of",
    "sort of",
    "yeah so",
    "so yeah",
    "okay so",
    "ok so",
    "alright so",
    "all right so",
    "right so",
    "um so",
    "uh so",
)

_FILLER_TOKENS = frozenset(
    {
        "um",
        "uh",
        "er",
        "ah",
        "huh",
        "hmm",
        "mhm",
        "mmhmm",
        "mm",
        "mmm",
        "like",
        "basically",
        "actually",
        "literally",
        "well",
    }
)

_GREETING_PHRASES = (
    "nice to meet you",
    "good morning",
    "good afternoon",
    "good evening",
    "how are you",
    "how is it going",
    "hows it going",
    "how's it going",
    "pleased to meet you",
    "thank you",
    "thanks so much",
    "can you hear me",
    "can you hear us",
    "am i audible",
    "are you there",
    "you there",
    "is this working",
    "mic check",
    "hello hello",
    "testing testing",
)

_GREETING_TOKENS = frozenset(
    {
        "hi",
        "hello",
        "hey",
        "thanks",
        "welcome",
        "bye",
        "goodbye",
        "cheers",
    }
)

_ACK_PHRASES = (
    "got it",
    "i see",
    "makes sense",
    "sounds good",
    "let us start",
    "lets start",
    "whenever you are ready",
    "whenever you're ready",
    "go ahead",
)

_ACK_TOKENS = frozenset(
    {
        "okay",
        "ok",
        "yeah",
        "yep",
        "yup",
        "yes",
        "nope",
        "right",
        "sure",
        "cool",
        "great",
        "awesome",
        "alright",
        "perfect",
        "nice",
        "good",
    }
)

_OVERLAP_MARKERS = (
    "[crosstalk]",
    "[inaudible]",
    "(inaudible)",
    "[overlap]",
    "[silence]",
    "crosstalk",
)

_FOLLOW_UP_PREFIXES = (
    "and ",
    "also ",
    "but ",
    "so then ",
    "what about ",
    "how about ",
    "now ",
    "next ",
    "similarly ",
    "okay and ",
    "and then ",
    "can you also ",
    "could you also ",
    "would you also ",
)

_FOLLOW_UP_PHRASES = (
    "go deeper",
    "dig deeper",
    "tell me more",
    "more detail",
    "in more detail",
    "more specifically",
    "can you expand",
    "could you expand",
    "follow up",
    "follow-up",
    "for example",
    "an example",
    "give an example",
    "tradeoff",
    "trade-off",
    "tradeoffs",
    "why is that",
    "how so",
    "same question",
    "one more",
    "another question",
    "what if ",
    "and how",
    "and why",
    "compared to",
    "versus ",
    " vs ",
)

_SHORT_ANAPHORA = frozenset(
    {
        "why",
        "why not",
        "how so",
        "how",
        "and then",
        "go on",
        "continue",
        "more",
        "example",
        "an example",
        "tradeoffs",
        "tradeoff",
        "complexity",
        "big o",
        "runtime",
        "edge cases",
        "edge case",
    }
)

_QUESTION_PREFIXES = (
    "what ",
    "why ",
    "how ",
    "when ",
    "where ",
    "which ",
    "who ",
    "whose ",
    "whom ",
    "can you ",
    "could you ",
    "would you ",
    "will you ",
    "do you ",
    "does ",
    "did you ",
    "is there ",
    "are there ",
    "tell me ",
    "tell us ",
    "explain ",
    "describe ",
    "walk me ",
    "walk us ",
    "compare ",
    "contrast ",
    "design ",
    "implement ",
    "write ",
    "code ",
    "build ",
    "discuss ",
    "give me ",
    "give an ",
    "name ",
    "list ",
    "define ",
    "outline ",
    "show ",
    "talk about ",
    "talk me ",
    "please ",
    "imagine ",
    "suppose ",
)

_FRAMING = frozenset(
    {
        "can",
        "could",
        "would",
        "will",
        "please",
        "tell",
        "explain",
        "describe",
        "walk",
        "talk",
        "discuss",
        "give",
        "show",
        "define",
        "outline",
        "name",
        "list",
    }
)

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "to",
        "of",
        "in",
        "on",
        "for",
        "and",
        "or",
        "but",
        "if",
        "it",
        "is",
        "are",
        "was",
        "be",
        "as",
        "at",
        "by",
        "with",
        "from",
        "that",
        "this",
        "these",
        "those",
        "you",
        "your",
        "we",
        "me",
        "us",
        "i",
        "my",
        "do",
        "does",
        "did",
        "have",
        "has",
        "had",
        "just",
        "about",
        "into",
        "than",
        "then",
        "so",
        "not",
        "no",
        "yes",
    }
)

_TRAILING_INCOMPLETE = frozenset(
    {"and", "or", "but", "so", "like", "the", "a", "an", "of", "to", "with", "for"}
)

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
_MULTI_SPACE = re.compile(r"\s+")
_CLAUSE_RE = re.compile(r"[.!?]+|\n+")


def classify_clip(
    transcript: str,
    answered: list[str] | None = None,
    *,
    typed: bool = False,
) -> ClipVerdict:
    raw = (transcript or "").strip()
    previous = [item for item in (answered or []) if (item or "").strip()]
    if not raw:
        return _skip("", "filler")

    extracted = raw if typed else _extract_question(raw)
    cleaned = _clean_speech(extracted)
    if not cleaned:
        return _skip(raw, "filler")

    if not typed:
        overlap = _has_overlap_marker(raw)
        kind = _non_question_reason(cleaned, previous)
        if kind is not None:
            if overlap and kind in {"filler", "greeting", "incomplete"}:
                return _skip(raw, "overlap")
            return _skip(extracted, kind)

    if _is_follow_up(cleaned, previous):
        return _answer(cleaned, "follow_up")

    if _is_duplicate(cleaned, previous):
        return ClipVerdict(
            action="skip",
            reason="duplicate",
            text=cleaned,
            detail=DETAILS["duplicate"],
            retryable=False,
        )

    return _answer(cleaned, "question")


def _skip(text: str, reason: Reason) -> ClipVerdict:
    return ClipVerdict(
        action="skip",
        reason=reason,
        text=text,
        detail=DETAILS[reason],
        retryable=True,
    )


def _answer(text: str, reason: Reason) -> ClipVerdict:
    return ClipVerdict(
        action="answer",
        reason=reason,
        text=text,
        detail=DETAILS[reason],
        retryable=True,
    )


def _extract_question(text: str) -> str:
    stripped = _strip_leading_filler(_strip_overlap_markers(text)).strip()
    if not stripped:
        return ""
    parts = _clause_parts(stripped)
    if len(parts) <= 1:
        return stripped

    start = 0
    while start < len(parts) and _is_chitchat_clause(parts[start][0]):
        start += 1
    end = len(parts)
    while end > start and _is_chitchat_clause(parts[end - 1][0]):
        end -= 1
    kept = parts[start:end]
    if not kept:
        return stripped
    return "".join(clause + sep for clause, sep in kept).strip()


def _clause_parts(text: str) -> list[tuple[str, str]]:
    parts: list[tuple[str, str]] = []
    last = 0
    for match in _CLAUSE_RE.finditer(text):
        clause = text[last : match.start()].strip(" ,;")
        if clause:
            parts.append((clause, match.group(0)))
        last = match.end()
    tail = text[last:].strip(" ,;")
    if tail:
        parts.append((tail, ""))
    return parts


def _is_chitchat_clause(clause: str) -> bool:
    cleaned = _clean_speech(clause)
    if not cleaned:
        return True
    folded = _fold(cleaned)
    tokens = _tokens(folded)
    if not tokens:
        return True
    if _looks_like_question(folded):
        return False
    if _is_greeting(folded, tokens):
        return True
    if all(token in _FILLER_TOKENS or token in _ACK_TOKENS for token in tokens):
        return True
    return False


def _non_question_reason(text: str, previous: list[str]) -> Reason | None:
    folded = _fold(text)
    tokens = _tokens(folded)
    if not tokens:
        return "filler"
    if _has_overlap_marker(text) and not _looks_like_question(folded):
        return "overlap"
    if _is_greeting(folded, tokens):
        return "greeting"
    if all(token in _FILLER_TOKENS or token in _ACK_TOKENS for token in tokens):
        return "filler"
    if previous and _is_follow_up(text, previous):
        return None
    if _is_incomplete(folded, tokens):
        return "incomplete"
    if _looks_like_question(folded):
        return None
    if len(_content_tokens(folded)) >= 2:
        return None
    if len(tokens) <= 6:
        return "not_a_question"
    return None


def _is_greeting(folded: str, tokens: list[str]) -> bool:
    if _is_only_chitchat(folded, _GREETING_PHRASES + _ACK_PHRASES):
        return True
    has_greeting = any(token in _GREETING_TOKENS for token in tokens)
    if has_greeting and all(
        token in _GREETING_TOKENS or token in _ACK_TOKENS or token in _FILLER_TOKENS for token in tokens
    ):
        return True
    return False


def _is_only_chitchat(folded: str, phrases: tuple[str, ...]) -> bool:
    stripped = folded.replace("?", "").strip()
    for phrase in phrases:
        if stripped == phrase:
            return True
        if stripped.startswith(phrase + " "):
            rest_tokens = _tokens(stripped[len(phrase) :].strip())
            if rest_tokens and all(
                token in _FILLER_TOKENS or token in _ACK_TOKENS or token in _GREETING_TOKENS for token in rest_tokens
            ):
                return True
            if not rest_tokens:
                return True
    return False


def _is_incomplete(folded: str, tokens: list[str]) -> bool:
    if folded.endswith("?"):
        return False
    if tokens and tokens[-1] in _TRAILING_INCOMPLETE:
        return True
    content = _content_tokens(folded)
    if folded.startswith(_QUESTION_PREFIXES) and len(content) < 2:
        return True
    return False


def _is_short_anaphora(folded: str) -> bool:
    stripped = folded.replace("?", "").strip()
    return stripped in _SHORT_ANAPHORA


def _is_follow_up(text: str, previous: list[str]) -> bool:
    if not previous:
        return False
    folded = _fold(text)
    if _is_short_anaphora(folded):
        return True
    if folded.startswith(_FOLLOW_UP_PREFIXES):
        return True
    return any(phrase in folded for phrase in _FOLLOW_UP_PHRASES)


def _is_duplicate(text: str, previous: list[str]) -> bool:
    folded = _fold(text)
    core = _core_phrase(folded)
    tokens = _content_tokens(folded)
    if not core:
        return False
    for item in previous:
        prev_folded = _fold(item)
        prev_core = _core_phrase(prev_folded)
        if not prev_core:
            continue
        if core == prev_core:
            return True
        if SequenceMatcher(None, core, prev_core).ratio() >= 0.88:
            return True
        prev_tokens = _content_tokens(prev_folded)
        if tokens and prev_tokens and tokens == prev_tokens:
            return True
        if tokens and prev_tokens:
            shared = tokens & prev_tokens
            union = tokens | prev_tokens
            if union and len(shared) / len(union) >= 0.85:
                return True
            smaller, larger = (tokens, prev_tokens) if len(tokens) <= len(prev_tokens) else (prev_tokens, tokens)
            extra = larger - smaller
            if smaller and smaller <= larger and not extra:
                return True
            if (
                smaller == tokens
                and smaller <= larger
                and len(tokens) >= 2
                and len(tokens) >= 0.75 * len(prev_tokens)
            ):
                # Near-equal restatement, not a narrower follow-up.
                return True
    return False


def _looks_like_question(folded: str) -> bool:
    if "?" in folded:
        return True
    return folded.startswith(_QUESTION_PREFIXES)


def _has_overlap_marker(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in _OVERLAP_MARKERS)


def _strip_overlap_markers(text: str) -> str:
    cleaned = text
    for marker in _OVERLAP_MARKERS:
        cleaned = re.sub(re.escape(marker), " ", cleaned, flags=re.IGNORECASE)
    return _MULTI_SPACE.sub(" ", cleaned).strip()


def _clean_speech(text: str) -> str:
    stripped = _strip_leading_filler(text).strip(" ,;")
    stripped = re.sub(r"\b(?:um+|uh+|er+|ah+)\b", " ", stripped, flags=re.IGNORECASE)
    return _MULTI_SPACE.sub(" ", stripped).strip(" ,;")


def _strip_leading_filler(text: str) -> str:
    current = text.strip()
    changed = True
    while current and changed:
        changed = False
        folded = current.casefold()
        for phrase in _FILLER_PHRASES:
            if folded.startswith(phrase + " ") or folded == phrase:
                current = current[len(phrase) :].lstrip(" ,")
                changed = True
                break
        if changed:
            continue
        match = re.match(r"^(?:um+|uh+|er+|ah+|well|so|yeah|yep|yup|okay|ok|right|alright)\b[,\s]*", current, flags=re.IGNORECASE)
        if match and match.end() < len(current):
            current = current[match.end() :]
            changed = True
    return current


def _fold(text: str) -> str:
    lowered = text.casefold()
    for src, dst in _CONTRACTIONS:
        lowered = lowered.replace(src, dst)
    lowered = re.sub(r"[^\w\s?]", " ", lowered)
    return _MULTI_SPACE.sub(" ", lowered).strip()


def _core_phrase(folded: str) -> str:
    tokens = [token for token in _tokens(folded) if token not in _FILLER_TOKENS and token not in _FRAMING]
    while tokens and tokens[0] in _STOPWORDS:
        tokens.pop(0)
    while tokens and tokens[-1] in _STOPWORDS:
        tokens.pop()
    return " ".join(tokens)


def _tokens(folded: str) -> list[str]:
    return _TOKEN_RE.findall(folded.replace("?", " "))


def _content_tokens(folded: str) -> frozenset[str]:
    tokens = [
        _singular(token)
        for token in _tokens(folded)
        if token not in _STOPWORDS and token not in _FILLER_TOKENS and token not in _FRAMING and token not in _ACK_TOKENS
    ]
    return frozenset(token for token in tokens if token)


def _singular(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


_KIND_TO_MODE = {
    "coding": "coding",
    "system_design": "system_design",
    "behavioral": "star",
    "technical": "spoken_45",
}

_CODING_PHRASES = (
    "implement ",
    "write a function",
    "write a method",
    "write code",
    "leetcode",
    "given an array",
    "given a string",
    "given a list",
    "given a tree",
    "linked list",
    "binary tree",
    "binary search tree",
    "two sum",
    "reverse a",
    "complete the function",
    "fix this code",
    "debug this",
    "example 1:",
    "constraints:",
    "time complexity of this",
    "return the",
    "lru cache",
    "invert a binary",
)

_DESIGN_PHRASES = (
    "design a",
    "design an",
    "how would you design",
    "how would you scale",
    "how would you shard",
    "system design",
    "architecture for",
    "url shortener",
    "rate limiter",
    "news feed",
    "chat system",
    "millions of users",
    "billion users",
    "high availability",
    "walk me through how you would shard",
    "walk me through how you would design",
)

_BEHAVIORAL_PHRASES = (
    "tell me about a time",
    "tell us about a time",
    "walk me through a time",
    "give an example of a time",
    "talk about a time",
    "describe a time",
    "conflict",
    "disagreement",
    "proud of",
    "biggest weakness",
    "why do you want",
    "why this company",
    "why us",
    "walk me through your resume",
    "worked with a difficult",
    "leadership",
    "you failed",
    "a failure",
    "when you failed",
)


def classify_question_kind(text: str, *, typed: bool = False) -> QuestionKind:
    folded = _fold(text)
    if not folded:
        return "technical"
    if _has_phrase(folded, _CODING_PHRASES) or (typed and _looks_like_coding_prompt(text)):
        return "coding"
    if _has_phrase(folded, _BEHAVIORAL_PHRASES):
        return "behavioral"
    if _has_phrase(folded, _DESIGN_PHRASES):
        return "system_design"
    return "technical"


def resolve_turn_mode(
    preference: str | None,
    question: str,
    *,
    source: str = "spoken",
) -> tuple[str, QuestionKind]:
    from .modes import is_auto_mode, normalize_answer_mode

    kind = classify_question_kind(question, typed=source == "typed")
    pref = normalize_answer_mode(preference)
    if not is_auto_mode(pref):
        return pref, kind
    return _KIND_TO_MODE[kind], kind


def _has_phrase(folded: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in folded for phrase in phrases)


def _looks_like_coding_prompt(text: str) -> bool:
    raw = text or ""
    if re.search(r"example\s*\d+\s*:", raw, flags=re.I):
        return True
    if re.search(r"^\s*constraints\s*:", raw, flags=re.I | re.M):
        return True
    if "```" in raw:
        return True
    return False
