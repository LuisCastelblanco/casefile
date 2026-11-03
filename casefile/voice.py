"""Muse Voice Transcribe: turn an ops voice note into text.

File endpoint: POST {VOICE_URL}?sessionId=<uuid>, multipart/form-data with
  - "request": JSON {"model", "mode", "audioEncoding", optional "languageBias", "keywords"}
  - "audio":   16-bit PCM mono WAV at 16 or 24 kHz (max 10 minutes / 32 MB)
Keyword biasing helps with domain jargon (recon, OTP, idempotency...).
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
import uuid
import wave

import httpx

from .config import VOICE_LANGUAGES, VOICE_MODEL, VOICE_URL


class VoiceError(RuntimeError):
    pass


def ensure_wav16k(data: bytes) -> bytes:
    """Return 16 kHz mono 16-bit WAV. Converts with ffmpeg when needed."""
    if data[:4] == b"RIFF":
        try:
            with wave.open(io.BytesIO(data)) as w:
                if w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getframerate() in (16000, 24000):
                    return data
        except wave.Error:
            pass
    if not shutil.which("ffmpeg"):
        raise VoiceError("Audio must be 16 kHz mono 16-bit WAV (install ffmpeg to convert other formats).")
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = os.path.join(tmp, "in"), os.path.join(tmp, "out.wav")
        with open(src, "wb") as fh:
            fh.write(data)
        proc = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-ac", "1", "-ar", "16000",
                               "-sample_fmt", "s16", dst], capture_output=True)
        if proc.returncode != 0:
            raise VoiceError(f"ffmpeg could not convert the audio: {proc.stderr.decode()[:300]}")
        with open(dst, "rb") as fh:
            return fh.read()


def _post(api_key: str, wav: bytes, request: dict) -> httpx.Response:
    files = {
        "request": (None, json.dumps(request), "application/json"),
        "audio": ("voice-note.wav", wav, "audio/wav"),
    }
    return httpx.post(VOICE_URL, params={"sessionId": str(uuid.uuid4())},
                      headers={"Authorization": f"Bearer {api_key}"}, files=files, timeout=120)


def transcribe(wav: bytes, keywords: list[str] | None = None) -> dict:
    api_key = os.getenv("MODEL_API_KEY", "")
    if not api_key:
        raise VoiceError("MODEL_API_KEY is not set")
    wav = ensure_wav16k(wav)
    request = {"model": VOICE_MODEL, "mode": "PUSH_TO_TALK", "audioEncoding": "WAV"}
    if VOICE_LANGUAGES:
        request["languageBias"] = VOICE_LANGUAGES
    if keywords:
        request["keywords"] = keywords[:50]
    resp = _post(api_key, wav, request)
    if resp.status_code == 400 and ("keywords" in request or "languageBias" in request):
        # Biasing fields are optional; retry plain so a schema mismatch never blocks the demo.
        resp = _post(api_key, wav, {k: v for k, v in request.items() if k not in ("keywords", "languageBias")})
        request["biasing_dropped"] = True
    if resp.status_code >= 400:
        raise VoiceError(f"Muse Voice Transcribe returned {resp.status_code}: {resp.text[:400]}")
    body = resp.json()
    text = body.get("transcript") or body.get("text") or " ".join(
        t.get("transcript") or t.get("text", "") for t in body.get("turns", []))
    return {"text": text.strip(), "turns": body.get("turns", []), "model": VOICE_MODEL,
            "keywords": keywords or [], "biasing_dropped": request.get("biasing_dropped", False)}
