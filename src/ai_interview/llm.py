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

from .modes import SYSTEM_PROMPT
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

logger = logging.getLogger("uvicorn.error")

CONTEXT_ROLE_CHARS = 200
CONTEXT_JD_CHARS = 6000
CONTEXT_RESUME_CHARS = 8000


def _clip(text: str, limit: int) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def context_block(
    *,
    role: str = "",
    company: str = "",
    job_description: str = "",
    resume: str = "",
) -> str:
    role_text = _clip(role, CONTEXT_ROLE_CHARS)
    company_text = _clip(company, CONTEXT_ROLE_CHARS)
    jd_text = _clip(job_description, CONTEXT_JD_CHARS)
    resume_text = _clip(resume, CONTEXT_RESUME_CHARS)
    if not any((role_text, company_text, jd_text, resume_text)):
        return ""
    lines = ["Candidate context for this interview:"]
    if role_text:
        lines.append(f"- Role: {role_text}")
    if company_text:
        lines.append(f"- Company: {company_text}")
    if jd_text:
        lines.extend(["", "Job description:", jd_text])
    if resume_text:
        lines.extend(["", "Resume / background:", resume_text])
    lines.extend(
        [
            "",
            "Use this only when relevant. Ground answers in the candidate's experience.",
            "Do not recite the resume or job description unless asked.",
        ]
    )
    return "\n".join(lines)


def build_system_prompt(
    base: str,
    *,
    role: str = "",
    company: str = "",
    job_description: str = "",
    resume: str = "",
) -> str:
    extra = context_block(
        role=role,
        company=company,
        job_description=job_description,
        resume=resume,
    )
    if not extra:
        return base
    return f"{base.rstrip()}\n\n{extra}"


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

    if _looks_like_json_object(text):
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
    if "```" not in text:
        match = re.search(r"QUESTION:\s*(.*?)\s*ANSWER:\s*(.*)", text, flags=re.DOTALL | re.IGNORECASE)
        if match:
            question = match.group(1).strip() or question
            answer = match.group(2).strip()
    if not answer:
        return CopilotTurn(stage="answer", question=question, latest_speech=question, answer="")
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
    if "```" in stripped:
        return stripped
    question, answer = _partial_qa(stripped)
    if answer:
        return answer
    if re.match(r"QUESTION:\s*", stripped, flags=re.IGNORECASE):
        return ""
    return stripped


def _history_messages(history: list[tuple[str, str]] | None) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for question, answer in history or []:
        q = (question or "").strip()
        if not q:
            continue
        messages.append({"role": "user", "content": q})
        messages.append({"role": "assistant", "content": (answer or "").strip() or "(no answer drafted)"})
    return messages


async def copilot_turn(
    client: AsyncOpenAI,
    transcript: str,
    on_answer: AnswerDelta | None = None,
    *,
    history: list[tuple[str, str]] | None = None,
    system_prompt: str | None = None,
    max_tokens: int | None = None,
) -> CopilotTurn:
    token_limit = LLM_MAX_TOKENS if max_tokens is None else max_tokens
    kwargs: dict = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt or SYSTEM_PROMPT},
            *_history_messages(history),
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
        try:
            async for chunk in stream:
                piece = _stream_text(chunk)
                if not piece:
                    continue
                raw_parts.append(piece)
                answer = _visible_answer("".join(raw_parts))
                if on_answer and answer and answer != last_answer:
                    last_answer = answer
                    await on_answer(answer)
        finally:
            closer = getattr(stream, "aclose", None)
            if closer is not None:
                try:
                    await closer()
                except Exception:
                    pass
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


def _looks_like_json_object(text: str) -> bool:
    stripped = (text or "").lstrip()
    return stripped.startswith("{") or stripped.startswith("```json")


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
