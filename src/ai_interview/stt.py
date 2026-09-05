from __future__ import annotations

import asyncio
import logging
import tempfile

import numpy as np
from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio

from .settings import STT_MODEL

logger = logging.getLogger("uvicorn.error")

_model: WhisperModel | None = None


def _load_model() -> WhisperModel:
    global _model
    if _model is None:
        name = STT_MODEL or "base"
        logger.info("loading local whisper model %s", name)
        _model = WhisperModel(name, device="cpu", compute_type="int8")
    return _model


def ensure_model() -> None:
    _load_model()


def _transcribe_bytes(payload: bytes, mime_type: str) -> str:
    suffix = ".webm"
    mime = (mime_type or "").lower()
    if "ogg" in mime:
        suffix = ".ogg"
    elif "wav" in mime:
        suffix = ".wav"
    elif "mp4" in mime or "m4a" in mime:
        suffix = ".m4a"
    elif "mpeg" in mime or "mp3" in mime:
        suffix = ".mp3"

    with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
        tmp.write(payload)
        tmp.flush()
        try:
            audio = decode_audio(tmp.name, sampling_rate=16000)
        except Exception:
            logger.exception("could not decode %s audio", suffix)
            return ""

    if audio is None or len(audio) == 0:
        return ""
    samples = np.asarray(audio, dtype=np.float32)
    if samples.ndim > 1:
        samples = samples.mean(axis=0)

    duration_s = float(len(samples)) / 16000.0
    segments, _info = _load_model().transcribe(
        samples,
        language="en",
        vad_filter=duration_s > 18,
        without_timestamps=True,
        beam_size=1,
        best_of=1,
        condition_on_previous_text=False,
    )
    return " ".join(segment.text.strip() for segment in segments).strip()


async def transcribe_local(payload: bytes, mime_type: str) -> str:
    return await asyncio.to_thread(_transcribe_bytes, payload, mime_type)
