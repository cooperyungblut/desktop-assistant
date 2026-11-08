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
import re
import wave
from typing import Protocol

from .config import TtsConfig
from .numbers import spoken_numbers
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
            samples, rate = await asyncio.to_thread(speak, self._kokoro, text, voice)
        return to_wav(samples, rate)


def speak(kokoro, text: str, voice: Voice):
    """Synthesize one sentence with set pauses at its commas and at its end.

    Left to itself, Kokoro's pause at a comma varies a lot (measured: 0.05 s
    after one comma, 0.4 s after the next in the same sentence). With
    voice.split_clauses each clause is made on its own, so every comma gets
    the same clause_pause. Returns (float32 samples, sample rate).
    """
    import numpy as np

    style = resolve_voice(kokoro, voice.kokoro_voice)
    text = spoken_numbers(text)  # "1909" -> "nineteen oh nine"; the chat keeps the digits
    chunks = split_clauses(text) if voice.split_clauses else [text]
    parts, rate = [], 24000
    for chunk in chunks:
        samples, rate = kokoro.create(chunk, voice=style, speed=voice.speed, lang=voice.lang)
        parts.append(pad_to_pause(samples, rate, pause_after(chunk, voice)))
    return np.concatenate(parts), rate


_CLAUSE_BREAK = re.compile(r"(?<=[,;:])\s+|\s+(?=[\u2013\u2014]\s)")


def split_clauses(text: str, min_chars: int = 10) -> list[str]:
    """Split a sentence after its commas, semicolons, colons and dashes.

    Very short pieces ("Okay," "so,") are kept with their neighbour: on their
    own they'd sound clipped. Commas inside numbers (1,000) have no space
    after them, so they never split.
    """
    pieces = [p for p in _CLAUSE_BREAK.split(text.strip()) if p]
    chunks: list[str] = []
    buf = ""
    for piece in pieces:
        buf = f"{buf} {piece}" if buf else piece
        if len(buf) >= min_chars:
            chunks.append(buf)
            buf = ""
    if buf:
        if chunks and len(buf) < min_chars:
            chunks[-1] = f"{chunks[-1]} {buf}"
        else:
            chunks.append(buf)
    return chunks


# Speech is made one sentence at a time, and Kokoro trims the silence off each
# clip, so back to back they leave only about 0.1 s between sentences and the
# reading sounds rushed. Each clip gets a deliberate pause at its end instead.
_SENTENCE_END = ".!?\u2026"
_CLAUSE_END = ",;:\u2013\u2014-"


def pause_after(text: str, voice: Voice) -> float:
    """Seconds of silence wanted after this chunk of speech."""
    end = text.rstrip().rstrip("\"')]\u201d\u2019")[-1:]
    if end and end in _CLAUSE_END:
        return voice.clause_pause
    return voice.sentence_pause  # a sentence end, or the last words of a reply


def trailing_silence(samples, rate: int, quiet_db: float = -40.0) -> float:
    """How many seconds at the end of the clip are already (near) silent."""
    import numpy as np

    frame = max(1, rate // 100)  # 10 ms
    n = len(samples) // frame * frame
    if n == 0:
        return 0.0
    loud = np.sqrt((np.asarray(samples[:n], dtype=np.float32).reshape(-1, frame) ** 2).mean(1))
    quiet = loud <= loud.max() * 10 ** (quiet_db / 20)
    if quiet.all():
        return len(samples) / rate
    tail = int(np.argmax(~quiet[::-1]))  # quiet frames before the last sound
    return (tail * frame + len(samples) - n) / rate


def pad_to_pause(samples, rate: int, pause: float):
    """Append silence so the clip ends with `pause` seconds of it (never trims)."""
    import numpy as np

    missing = pause - trailing_silence(samples, rate)
    if missing <= 0:
        return samples
    return np.concatenate([samples, np.zeros(int(missing * rate), dtype=samples.dtype)])


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
