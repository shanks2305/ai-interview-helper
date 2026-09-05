from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from .paths import env_file, project_root

ROOT_DIR = project_root()
load_dotenv(env_file())
load_dotenv()


def _first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default


@dataclass(frozen=True)
class ProviderPreset:
    name: str
    base_url: str | None
    default_chat_model: str
    default_stt_model: str
    requires_key: bool
    alt_keys: tuple[str, ...]


PRESETS: dict[str, ProviderPreset] = {
    "openai": ProviderPreset(
        name="openai",
        base_url=None,
        default_chat_model="gpt-4o-mini",
        default_stt_model="gpt-4o-mini-transcribe",
        requires_key=True,
        alt_keys=("OPENAI_API_KEY",),
    ),
    "gemini": ProviderPreset(
        name="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        default_chat_model="gemini-2.0-flash",
        default_stt_model="gemini-2.0-flash",
        requires_key=True,
        alt_keys=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    ),
    "ollama": ProviderPreset(
        name="ollama",
        base_url="http://127.0.0.1:11434/v1",
        default_chat_model="llama3.1:8b",
        default_stt_model="whisper",
        requires_key=False,
        alt_keys=(),
    ),
    "groq": ProviderPreset(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        default_chat_model="llama-3.3-70b-versatile",
        default_stt_model="whisper-large-v3",
        requires_key=True,
        alt_keys=("GROQ_API_KEY",),
    ),
    "custom": ProviderPreset(
        name="custom",
        base_url=None,
        default_chat_model="gpt-4o-mini",
        default_stt_model="whisper-1",
        requires_key=False,
        alt_keys=("OPENAI_API_KEY",),
    ),
    "local": ProviderPreset(
        name="local",
        base_url=None,
        default_chat_model="",
        default_stt_model="base",
        requires_key=False,
        alt_keys=(),
    ),
}


def _preset(name: str) -> ProviderPreset:
    key = (name or "openai").strip().lower()
    if key not in PRESETS:
        known = ", ".join(sorted(PRESETS))
        raise ValueError(f"Unknown provider '{name}'. Use one of: {known}")
    return PRESETS[key]


LLM_PROVIDER = _preset(_first("LLM_PROVIDER", default="openai")).name
STT_PROVIDER = _preset(_first("STT_PROVIDER", default=LLM_PROVIDER)).name

_llm_preset = PRESETS[LLM_PROVIDER]
_stt_preset = PRESETS[STT_PROVIDER]

LLM_BASE_URL = _first("LLM_BASE_URL") or _llm_preset.base_url
if _first("STT_BASE_URL"):
    STT_BASE_URL = _first("STT_BASE_URL")
elif STT_PROVIDER == LLM_PROVIDER:
    STT_BASE_URL = LLM_BASE_URL
else:
    STT_BASE_URL = _stt_preset.base_url

LLM_API_KEY = _first("LLM_API_KEY", *_llm_preset.alt_keys)
STT_API_KEY = _first("STT_API_KEY", *_stt_preset.alt_keys)
if not STT_API_KEY and STT_PROVIDER == LLM_PROVIDER:
    STT_API_KEY = LLM_API_KEY

LLM_MODEL = _first(
    "LLM_MODEL",
    "OPENAI_CHAT_MODEL",
    default=_llm_preset.default_chat_model,
)
STT_MODEL = _first(
    "STT_MODEL",
    "OPENAI_TRANSCRIBE_MODEL",
    default=_stt_preset.default_stt_model,
)

_temperature_raw = os.getenv("LLM_TEMPERATURE", "0.3")
LLM_TEMPERATURE = None if _temperature_raw.strip() == "" else float(_temperature_raw)

_max_tokens_raw = os.getenv("LLM_MAX_TOKENS", "320").strip()
LLM_MAX_TOKENS = None if _max_tokens_raw == "" else int(_max_tokens_raw)

TRANSCRIBE_INTERVAL_SECONDS = float(os.getenv("TRANSCRIBE_INTERVAL_SECONDS", "1.0"))
MIN_AUDIO_BYTES = int(os.getenv("MIN_AUDIO_BYTES", "2500"))
UTTERANCE_STABLE_TICKS = int(os.getenv("UTTERANCE_STABLE_TICKS", "1"))
MAX_AUDIO_WINDOW_BYTES = int(os.getenv("MAX_AUDIO_WINDOW_BYTES", "80000"))


def chat_ready() -> bool:
    return bool(LLM_API_KEY) or not _llm_preset.requires_key


def stt_ready() -> bool:
    return bool(STT_API_KEY) or not _stt_preset.requires_key


def api_ready() -> bool:
    return chat_ready() and stt_ready()


def readiness_error() -> str | None:
    if not chat_ready():
        keys = " or ".join(("LLM_API_KEY",) + _llm_preset.alt_keys)
        return f"{LLM_PROVIDER} chat needs an API key ({keys})."
    if not stt_ready():
        keys = " or ".join(("STT_API_KEY",) + _stt_preset.alt_keys)
        return f"{STT_PROVIDER} transcription needs an API key ({keys})."
    return None
