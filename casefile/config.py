"""Runtime configuration, read from environment variables (and an optional .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

PROVIDERS = {
    # Meta Model API (OpenAI-compatible Chat Completions)
    "meta": {
        "base_url": os.getenv("META_BASE_URL", "https://api.meta.ai/v1"),
        "key_env": "MODEL_API_KEY",
        "default_model": "muse-spark-1.3",
    },
    # Muse Spark Open (open weights) served by Baseten Model APIs
    "baseten": {
        "base_url": os.getenv("BASETEN_BASE_URL", "https://inference.baseten.co/v1"),
        "key_env": "BASETEN_API_KEY",
        "default_model": "meta-models/muse-spark-open-1026",
    },
    # Any other OpenAI-compatible server running Muse Spark Open / Muse Glimmer
    # (vLLM, SGLang, Modal endpoint...). Set OPENAI_COMPAT_BASE_URL.
    "openai_compat": {
        "base_url": os.getenv("OPENAI_COMPAT_BASE_URL", "http://localhost:8000/v1"),
        "key_env": "OPENAI_COMPAT_API_KEY",
        "default_model": os.getenv("OPENAI_COMPAT_MODEL", "muse-spark-open"),
    },
    # Offline scripted model, only for tests and UI development
    "mock": {"base_url": "", "key_env": "", "default_model": "mock"},
}


@dataclass
class ModelConfig:
    provider: str
    model: str
    base_url: str
    api_key: str
    reasoning_effort: str | None

    @property
    def label(self) -> str:
        names = {"meta": "Meta Model API", "baseten": "Baseten", "openai_compat": "self-hosted", "mock": "offline mock"}
        return f"{self.model} · {names.get(self.provider, self.provider)}"


def model_config(role: str) -> ModelConfig:
    """role is AGENT, VERIFIER or VISION; each can point at a different provider."""
    provider = os.getenv(f"{role}_PROVIDER") or os.getenv("LLM_PROVIDER", "meta")
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider {provider!r}; use one of {sorted(PROVIDERS)}")
    p = PROVIDERS[provider]
    model = os.getenv(f"{role}_MODEL") or p["default_model"]
    effort = os.getenv(f"{role}_REASONING_EFFORT", os.getenv("REASONING_EFFORT", "medium" if provider == "meta" else ""))
    return ModelConfig(
        provider=provider,
        model=model,
        base_url=p["base_url"],
        api_key=os.getenv(p["key_env"], "") if p["key_env"] else "",
        reasoning_effort=effort or None,
    )


VOICE_MODEL = os.getenv("VOICE_MODEL", "muse-voice-transcribe-1.0")
VOICE_URL = os.getenv("VOICE_URL", "https://api.meta.ai/v1/asr/transcribe")
VOICE_LANGUAGES = [s.strip() for s in os.getenv("VOICE_LANGUAGES", "English,Spanish").split(",") if s.strip()]
MAX_AGENT_TURNS = int(os.getenv("MAX_AGENT_TURNS", "18"))
MAX_REVISIONS = int(os.getenv("MAX_REVISIONS", "1"))
RUNS_DIR = ROOT / "runs"
SANDBOX_DIR = ROOT / "sandbox"
