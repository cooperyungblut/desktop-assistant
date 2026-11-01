"""Text-to-speech. Kokoro (82M params) through ONNX Runtime on the CPU.

Why kokoro-onnx instead of the PyTorch `kokoro` package: no 2-3 GB torch
install, no system espeak-ng on Windows (espeakng-loader bundles it), and it
runs faster than real time on a modern CPU, which leaves the GPU's VRAM to
the chat model and Whisper.
"""

from __future__ import annotations

import asyncio
import io
import logging
import wave
from typing import Protocol

from .config import TtsConfig
from .persona import Voice

log = logging.getLogger(__name__)


class Speaker(Protocol):
    available: bool

    async def synthesize(self, text: str, voice: Voice) -> bytes | None:
        """Return a WAV file for `text`, or None if speech is unavailable."""


class SilentSpeaker:
    """Text-only mode: used when TTS is disabled or fails to load."""

    available = False

    async def synthesize(self, text: str, voice: Voice) -> bytes | None:
        return None


class KokoroSpeaker:
    available = True

    def __init__(self, config: TtsConfig):
        from kokoro_onnx import Kokoro  # imported lazily: optional dependency

        self._kokoro = Kokoro(str(config.model_path), str(config.voices_path))
        self._lock = asyncio.Lock()  # one synthesis at a time keeps CPU use predictable

    async def synthesize(self, text: str, voice: Voice) -> bytes | None:
        async with self._lock:
            samples, rate = await asyncio.to_thread(
                self._kokoro.create,
                text,
                voice=resolve_voice(self._kokoro, voice.kokoro_voice),
                speed=voice.speed,
                lang=voice.lang,
            )
        return to_wav(samples, rate)


def parse_voice_spec(spec: str) -> list[tuple[str, float]]:
    """ "af_heart" -> [("af_heart", 1.0)]; "af_heart:70, af_bella:30" -> weights summing to 1."""
    parts = []
    for item in spec.split(","):
        name, _, weight = item.strip().partition(":")
        if not name:
            raise ValueError(f"bad voice spec {spec!r}")
        try:
            w = float(weight) if weight else 1.0
        except ValueError:
            raise ValueError(f"bad weight in voice spec {spec!r}") from None
        if w <= 0:
            raise ValueError(f"weights must be positive in voice spec {spec!r}")
        parts.append((name.strip(), w))
    total = sum(w for _, w in parts)
    return [(name, w / total) for name, w in parts]


def resolve_voice(kokoro, spec: str):
    """A plain voice name passes through; a blend becomes a weighted average style."""
    parts = parse_voice_spec(spec)
    if len(parts) == 1:
        return parts[0][0]
    return sum(kokoro.get_voice_style(name) * w for name, w in parts)


def to_wav(samples, rate: int) -> bytes:
    """float32 samples in [-1, 1] -> 16-bit mono WAV bytes."""
    import numpy as np

    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def make_speaker(config: TtsConfig) -> Speaker:
    if config.engine == "none":
        return SilentSpeaker()
    if config.engine != "kokoro":
        raise ValueError(f"unknown tts engine {config.engine!r}")
    missing = [p for p in (config.model_path, config.voices_path) if not p.is_file()]
    if missing:
        log.warning(
            "Kokoro model files not found (%s). Running text-only. "
            "Run: python scripts/download_models.py",
            ", ".join(map(str, missing)),
        )
        return SilentSpeaker()
    try:
        return KokoroSpeaker(config)
    except ImportError:
        log.warning("kokoro-onnx isn't installed. Running text-only. Run: uv sync --extra tts")
        return SilentSpeaker()
