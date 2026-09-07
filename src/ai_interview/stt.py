from __future__ import annotations

import asyncio
import logging
import sys
import tempfile

import numpy as np
from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio

from .settings import STT_COMPUTE_TYPE, STT_DEVICE, STT_MODEL

logger = logging.getLogger("uvicorn.error")

_model: WhisperModel | None = None
_backend: str | None = None
_mlx_repo: str | None = None

MLX_REPOS = {
    "tiny": "mlx-community/whisper-tiny-mlx",
    "tiny.en": "mlx-community/whisper-tiny.en-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "base.en": "mlx-community/whisper-base.en-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "small.en": "mlx-community/whisper-small.en-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "medium.en": "mlx-community/whisper-medium.en-mlx",
    "large": "mlx-community/whisper-large-v3-mlx",
    "large-v2": "mlx-community/whisper-large-v2-mlx",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
}


def mlx_repo_for(model_name: str) -> str:
    name = (model_name or "base").strip()
    if "/" in name:
        return name
    return MLX_REPOS.get(name.lower(), name)


def cuda_device_count() -> int:
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count())
    except Exception:
        return 0


def mlx_importable() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        import mlx_whisper  # noqa: F401
    except Exception:
        return False
    return True


def resolve_whisper_runtime(
    requested_device: str = "auto",
    requested_compute: str = "",
    *,
    cuda_devices: int | None = None,
    mlx_available: bool | None = None,
) -> tuple[str, str]:
    device = (requested_device or "auto").strip().lower() or "auto"
    compute = (requested_compute or "").strip().lower()
    if device in {"metal", "gpu-metal"}:
        device = "mlx"
    if device == "auto":
        n_cuda = cuda_device_count() if cuda_devices is None else cuda_devices
        if n_cuda > 0:
            device = "cuda"
        elif mlx_importable() if mlx_available is None else mlx_available:
            device = "mlx"
        else:
            device = "cpu"
    if not compute:
        if device in {"cuda", "mlx"}:
            compute = "float16"
        else:
            compute = "int8"
    return device, compute


def _ensure_runtime() -> None:
    global _model, _backend, _mlx_repo
    if _backend is not None:
        return
    name = STT_MODEL or "base"
    device, compute = resolve_whisper_runtime(STT_DEVICE, STT_COMPUTE_TYPE)
    _backend = device
    logger.info("loading local whisper model %s device=%s compute=%s", name, device, compute)
    if device == "mlx":
        _mlx_repo = mlx_repo_for(name)
        return
    _model = WhisperModel(name, device=device, compute_type=compute)


def ensure_model() -> None:
    _ensure_runtime()


def _transcribe_mlx(samples: np.ndarray) -> str:
    import mlx_whisper

    repo = _mlx_repo or mlx_repo_for(STT_MODEL or "base")
    result = mlx_whisper.transcribe(
        samples,
        path_or_hf_repo=repo,
        language="en",
        word_timestamps=False,
    )
    if isinstance(result, dict):
        return str(result.get("text") or "").strip()
    return str(getattr(result, "text", "") or "").strip()


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
    _ensure_runtime()
    if _backend == "mlx":
        return _transcribe_mlx(samples)
    if _model is None:
        return ""

    segments, _info = _model.transcribe(
        samples,
        language="en",
        vad_filter=duration_s > 180,
        without_timestamps=True,
        beam_size=1,
        best_of=1,
        condition_on_previous_text=False,
    )
    return " ".join(segment.text.strip() for segment in segments).strip()


async def transcribe_local(payload: bytes, mime_type: str) -> str:
    return await asyncio.to_thread(_transcribe_bytes, payload, mime_type)
