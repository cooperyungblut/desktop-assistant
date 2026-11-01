"""Speech-to-text with faster-whisper (Whisper on CTranslate2).

Runs on the CPU by default. On this project's GPU budget the chat model
already takes most of the 16 GB, and `small.en` with int8 on a desktop CPU
transcribes a short question in about a second. CUDA is an option in
config.yaml (see docs/setup-windows.md, Chunk D).

The browser sends whatever MediaRecorder produced (WebM/Opus in Chromium).
`decode_audio` turns it into 16 kHz mono samples with PyAV (installed with
faster-whisper), so there's no separate ffmpeg install. We decode ourselves
instead of using faster-whisper's helper because that helper passes an option
newer PyAV releases removed.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import sys
from pathlib import Path
from typing import Protocol

from .config import SttConfig

log = logging.getLogger(__name__)
SAMPLE_RATE = 16000  # what Whisper expects

# Whisper was trained on subtitled video, so near-silence sometimes comes out
# as one of these. The VAD filter removes most silence; this catches the rest.
_SILENCE_HALLUCINATIONS = {
    "you",
    "thanks for watching",
    "thank you for watching",
    "subtitles by the amaraorg community",
}


class SttUnavailable(RuntimeError):
    pass


class Transcriber(Protocol):
    available: bool

    async def load(self) -> None:
        """Prepare the model. Never raises; sets `available` on success."""

    async def transcribe(self, audio: bytes, hints: str | None = None) -> str:
        """Return the text spoken in `audio` (any format PyAV can decode).

        `hints` is text the speaker is likely to use, such as names from the
        current brief; Whisper favors those spellings.
        """


class NoTranscriber:
    available = False

    async def load(self) -> None:
        pass

    async def transcribe(self, audio: bytes, hints: str | None = None) -> str:
        raise SttUnavailable("speech input is turned off (stt.engine is 'none')")


def clean_transcript(text: str) -> str:
    text = " ".join(text.split())
    key = re.sub(r"[^a-z ]", "", text.lower()).strip()
    return "" if not key or key in _SILENCE_HALLUCINATIONS else text


class WhisperTranscriber:
    def __init__(self, config: SttConfig):
        self._config = config
        self._model = None
        self._lock = asyncio.Lock()
        self.available = False
        self.error: str | None = None

    async def load(self) -> None:
        """Load the model in a worker thread (first run downloads ~250-500 MB)."""
        try:
            await asyncio.to_thread(self._load_with_fallback)
            self.available = True
        except Exception as exc:  # missing package, download failure, ...
            self.error = str(exc)
            log.warning("Speech input disabled: %s", exc)

    def _load_with_fallback(self) -> None:
        cfg = self._config
        if cfg.device == "cuda":
            try:
                self._model = self._load(cfg.device, cfg.compute_type)
                return
            except Exception as exc:
                log.warning("Whisper on CUDA failed (%s). Falling back to CPU.", exc)
        self._model = self._load("cpu", "int8" if cfg.device == "cuda" else cfg.compute_type)

    def _load(self, device: str, compute_type: str):
        if device == "cuda":
            _add_nvidia_dll_dirs()
        import numpy as np
        from faster_whisper import WhisperModel

        model = WhisperModel(
            self._config.model,
            device=device,
            compute_type=compute_type,
            download_root=str(self._config.download_dir),
        )
        # CUDA libraries load lazily, so a missing DLL only shows up on the first
        # real inference. Run one on a second of silence to find out now.
        segments, _ = model.transcribe(np.zeros(SAMPLE_RATE, dtype=np.float32), language="en")
        list(segments)
        log.info("Whisper %s ready on %s (%s)", self._config.model, device, compute_type)
        return model

    async def transcribe(self, audio: bytes, hints: str | None = None) -> str:
        if self._model is None:
            raise SttUnavailable(self.error or "speech recognition is still loading")
        async with self._lock:
            return await asyncio.to_thread(self._transcribe_sync, audio, hints)

    def _transcribe_sync(self, audio: bytes, hints: str | None) -> str:
        samples = decode_audio(audio)
        if samples.size < SAMPLE_RATE // 4:  # under a quarter second: nothing to hear
            return ""
        segments, _ = self._model.transcribe(
            samples,
            language=self._config.language,
            beam_size=5,
            vad_filter=True,  # skip leading/trailing silence and breaths
            condition_on_previous_text=False,
            initial_prompt=hints,  # e.g. "Names: Northwind Traders, Dana Reyes, ..."
        )
        return clean_transcript(" ".join(s.text for s in segments))


def decode_audio(audio: bytes):
    """Any container/codec PyAV understands -> float32 mono samples at 16 kHz."""
    import av
    import numpy as np

    resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    chunks = []
    try:
        with av.open(io.BytesIO(audio), mode="r") as container:
            for frame in container.decode(audio=0):
                chunks += [f.to_ndarray().reshape(-1) for f in resampler.resample(frame)]
            chunks += [f.to_ndarray().reshape(-1) for f in resampler.resample(None)]
    except av.FFmpegError as exc:
        raise SttUnavailable(f"couldn't decode the recording: {exc}") from exc
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def make_transcriber(config: SttConfig) -> Transcriber:
    if config.engine == "none":
        return NoTranscriber()
    if config.engine != "whisper":
        raise ValueError(f"unknown stt engine {config.engine!r}")
    return WhisperTranscriber(config)


def _add_nvidia_dll_dirs() -> None:
    """Make pip-installed CUDA libraries (nvidia-cublas-cu12 etc.) loadable on Windows."""
    if sys.platform != "win32":
        return
    try:
        import nvidia  # namespace package provided by the nvidia-* wheels
    except ImportError:
        return
    for base in nvidia.__path__:
        for bin_dir in Path(base).glob("*/bin"):
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
