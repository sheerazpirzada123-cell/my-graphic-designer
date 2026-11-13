"""Speech -> text for voice instructions in (almost) any language.

Default engine: faster-whisper, running locally (free, private, no API key).
Alternative: OpenAI transcription API (set STT_ENGINE=openai and OPENAI_API_KEY).
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


class TranscribeError(RuntimeError):
    pass


@lru_cache(maxsize=2)
def _whisper(model_size: str):
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise TranscribeError("faster-whisper is not installed. Run: pip install faster-whisper") from e
    return WhisperModel(model_size, device="auto", compute_type="int8")


def transcribe(path: str | Path, language: str | None = None, model_size: str | None = None) -> tuple[str, str | None]:
    """Return (text, detected_language). `language` is an optional ISO code like 'ur', 'hi', 'en', 'pa', 'ar'."""
    path = Path(path)
    if not path.is_file():
        raise TranscribeError(f"Audio file not found: {path}")
    engine = os.environ.get("STT_ENGINE", "local").lower()
    if engine == "openai":
        return _openai(path, language)

    size = model_size or os.environ.get("WHISPER_MODEL", "small")
    model = _whisper(size)
    segments, info = model.transcribe(str(path), language=language, task="transcribe", vad_filter=True, beam_size=5)
    text = " ".join(s.text.strip() for s in segments).strip()
    if not text:
        raise TranscribeError("No speech was recognised in the recording.")
    return text, info.language


def _openai(path: Path, language: str | None) -> tuple[str, str | None]:
    try:
        from openai import OpenAI
    except ImportError as e:
        raise TranscribeError("openai package not installed. Run: pip install openai") from e
    client = OpenAI()
    with open(path, "rb") as f:
        kwargs = {"model": os.environ.get("STT_MODEL", "whisper-1"), "file": f}
        if language:
            kwargs["language"] = language
        res = client.audio.transcriptions.create(**kwargs)
    text = (res.text or "").strip()
    if not text:
        raise TranscribeError("No speech was recognised in the recording.")
    return text, language
