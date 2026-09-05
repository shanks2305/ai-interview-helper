from __future__ import annotations

import io
import json
import logging
import re
from dataclasses import dataclass
from typing import Literal

from collections.abc import Awaitable, Callable

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from .settings import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MAX_TOKENS,
    LLM_MODEL,
    LLM_TEMPERATURE,
    STT_API_KEY,
    STT_BASE_URL,
    STT_MODEL,
    STT_PROVIDER,
    readiness_error,
)

AnswerDelta = Callable[[str], Awaitable[None]]
QuestionReady = Callable[[str], Awaitable[None]]

logger = logging.getLogger("uvicorn.error")

SYSTEM_PROMPT = """You are a senior software engineer answering a live interview question.

The user message is the interviewer's question. Start the spoken answer immediately.
Do not repeat the question. No labels, no preamble, no markdown.

Sound like a senior engineer in the room:
1. Open with a precise definition (what it is, and what it is not if that helps).
2. Give one concrete production-style example (name the pieces: API, DB, queue, UI).
3. Call out the trade-off or when you would not use it.
Keep it tight: 4-6 sentences.
"""

TYPED_PROMPT = """You are a senior software engineer answering a typed interview question.
It may be a coding problem, system design prompt, or anything pasted from a screen.

The user message is the full question. Answer so a candidate can speak it and write it.

1. Restate the goal in one sentence.
2. Name the approach and time/space complexity when it is a coding problem.
3. If code is needed, give a complete solution in a fenced code block (Python unless another language is specified).
4. Walk through one example and one edge case.

Be concrete. Prefer working code over theory.
"""


class CopilotTurn(BaseModel):
    stage: Literal["listening", "not_a_question", "answer"]
    latest_speech: str = ""
    question: str = ""
    answer: str = ""


@dataclass
class ModelClients:
    chat: AsyncOpenAI
    stt: AsyncOpenAI | None

    async def close(self) -> None:
        await self.chat.close()
        if self.stt is not None and self.stt is not self.chat:
            await self.stt.close()


def _client(api_key: str, base_url: str | None) -> AsyncOpenAI:
    kwargs: dict[str, str] = {"api_key": api_key or "local"}
    if base_url:
        kwargs["base_url"] = base_url
    return AsyncOpenAI(**kwargs)


def new_clients() -> ModelClients:
    error = readiness_error()
    if error:
        raise RuntimeError(error)
    chat = _client(LLM_API_KEY, LLM_BASE_URL)
    if STT_PROVIDER == "local":
        return ModelClients(chat=chat, stt=None)
    same_endpoint = (LLM_API_KEY == STT_API_KEY) and (LLM_BASE_URL == STT_BASE_URL)
    stt = chat if same_endpoint else _client(STT_API_KEY, STT_BASE_URL)
    return ModelClients(chat=chat, stt=stt)


def audio_filename(mime_type: str) -> str:
    mime = (mime_type or "").split(";", 1)[0].strip().lower()
    if "ogg" in mime:
        return "audio.ogg"
    if "mp4" in mime or "m4a" in mime:
        return "audio.m4a"
    if "mpeg" in mime or "mp3" in mime:
        return "audio.mp3"
    if "wav" in mime:
        return "audio.wav"
    return "audio.webm"


async def transcribe_audio(client: AsyncOpenAI | None, payload: bytes, mime_type: str) -> str:
    if STT_PROVIDER == "local":
        from .stt import transcribe_local

        return await transcribe_local(payload, mime_type)
    if client is None:
        raise RuntimeError("No speech-to-text client configured")
    buffer = io.BytesIO(payload)
    buffer.name = audio_filename(mime_type)
    kwargs: dict = {"model": STT_MODEL, "file": buffer}
    if STT_PROVIDER in {"openai", "groq"}:
        kwargs["language"] = "en"
    result = await client.audio.transcriptions.create(**kwargs)
    return (getattr(result, "text", "") or "").strip()


def _message_text(completion) -> str:
    message = completion.choices[0].message
    parts = [
        getattr(message, "content", None) or "",
        getattr(message, "reasoning", None) or "",
    ]
    return "\n".join(part for part in parts if part).strip()


def _parse_turn(raw: str, transcript: str) -> CopilotTurn:
    text = (raw or "").strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
    if not text:
        return CopilotTurn(stage="answer", question=transcript.strip(), answer="")
    if re.match(r"NOT_A_QUESTION\b", text, flags=re.IGNORECASE):
        return CopilotTurn(stage="not_a_question", question=transcript.strip(), latest_speech=transcript.strip())

    try:
        data = _extract_json(text)
        stage = data.get("stage") or "answer"
        if stage == "listening":
            stage = "answer"
        parsed = CopilotTurn(
            stage=stage if stage in {"not_a_question", "answer"} else "answer",
            latest_speech=str(data.get("latest_speech") or transcript),
            question=str(data.get("question") or transcript).strip(),
            answer=str(data.get("answer") or "").strip(),
        )
        if parsed.stage != "answer" or parsed.answer:
            return parsed
    except (json.JSONDecodeError, ValidationError, TypeError):
        pass

    question = transcript.strip()
    answer = text
    match = re.search(r"QUESTION:\s*(.*?)\s*ANSWER:\s*(.*)", text, flags=re.DOTALL | re.IGNORECASE)
    if match:
        question = match.group(1).strip() or question
        answer = match.group(2).strip()
    if not answer:
        return CopilotTurn(stage="not_a_question", question=question, latest_speech=question)
    return CopilotTurn(stage="answer", question=question, latest_speech=question, answer=answer)


def _partial_qa(raw: str) -> tuple[str, str]:
    match = re.search(r"QUESTION:\s*(.*?)\s*ANSWER:\s*(.*)", raw, flags=re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    answer_match = re.search(r"ANSWER:\s*(.*)", raw, flags=re.DOTALL | re.IGNORECASE)
    if answer_match:
        return "", answer_match.group(1).strip()
    return "", ""


def _visible_answer(raw: str) -> str:
    stripped = (raw or "").strip()
    if re.match(r"NOT_A_QUESTION\b", stripped, flags=re.IGNORECASE):
        return ""
    question, answer = _partial_qa(stripped)
    if answer:
        return answer
    if re.match(r"QUESTION:\s*", stripped, flags=re.IGNORECASE):
        return ""
    return stripped


async def copilot_turn(
    client: AsyncOpenAI,
    transcript: str,
    already_handled: list[str],
    on_question: QuestionReady | None = None,
    on_answer: AnswerDelta | None = None,
    *,
    system_prompt: str | None = None,
    max_tokens: int | None = None,
) -> CopilotTurn:
    _ = already_handled, on_question
    token_limit = LLM_MAX_TOKENS if max_tokens is None else max_tokens
    kwargs: dict = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt or SYSTEM_PROMPT},
            {
                "role": "user",
                "content": transcript.strip() or "(silence)",
            },
        ],
        "stream": True,
    }
    if LLM_TEMPERATURE is not None:
        kwargs["temperature"] = LLM_TEMPERATURE
    if token_limit is not None:
        kwargs["max_tokens"] = token_limit

    raw_parts: list[str] = []
    last_answer = ""
    try:
        stream = await client.chat.completions.create(**kwargs)
        async for chunk in stream:
            piece = _stream_text(chunk)
            if not piece:
                continue
            raw_parts.append(piece)
            answer = _visible_answer("".join(raw_parts))
            if on_answer and answer and answer != last_answer:
                last_answer = answer
                await on_answer(answer)
        raw = "".join(raw_parts).strip()
    except Exception:
        logger.exception("streaming llm failed; retrying without stream")
        kwargs.pop("stream", None)
        completion = await client.chat.completions.create(**kwargs)
        raw = _message_text(completion)
        answer = _visible_answer(raw)
        if on_answer and answer:
            await on_answer(answer)

    logger.info("llm raw=%s", raw[:500].replace("\n", " "))
    return _parse_turn(raw, transcript)


def _stream_text(chunk) -> str:
    choices = getattr(chunk, "choices", None) or []
    if not choices:
        return ""
    delta = getattr(choices[0], "delta", None)
    if delta is None:
        return ""
    return (getattr(delta, "content", None) or "").strip("\x00")


def _extract_json(text: str) -> dict:
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        data = json.loads(stripped)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
    if not match:
        raise json.JSONDecodeError("no json object", stripped, 0)
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise json.JSONDecodeError("json was not an object", stripped, 0)
    return data
