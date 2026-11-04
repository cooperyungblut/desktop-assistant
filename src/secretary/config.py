"""Local app configuration (paths, ports, model names).

The real config lives in `config.yaml`, which is git-ignored because it holds
personal paths such as the Google Drive folder. `config.example.yaml` is the
committed template.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class OllamaConfig(BaseModel):
    url: str = "http://127.0.0.1:11434"
    model: str = "gemma4:12b"
    # How long Ollama keeps the model in VRAM after a request: a duration such as
    # "30m", or -1 to keep it loaded for as long as the app runs. After it times
    # out, the next question waits for a reload (about 20 s for gemma4:12b); the
    # tradeoff is ~8 GB of VRAM held while idle. The app unloads it on quit.
    keep_alive: str | int = "30m"
    unload_on_exit: bool = True
    # Thinking models (Gemma 4, Qwen3, Qwen3.5) reason silently before answering,
    # which adds many seconds before the first word. Off by default for a voice
    # assistant. None leaves the flag out and uses the model's own default.
    think: bool | None = False
    timeout_seconds: float = 120.0


class TtsConfig(BaseModel):
    engine: str = "kokoro"  # "kokoro" or "none" (text only)
    model_path: Path = Path("models/kokoro-v1.0.onnx")
    voices_path: Path = Path("models/voices-v1.0.bin")


class SttConfig(BaseModel):
    engine: str = "whisper"  # "whisper" or "none" (typed questions only)
    model: str = "small.en"
    device: str = "cpu"  # "cpu" or "cuda"
    compute_type: str = "int8"  # int8 on CPU; int8_float16 on CUDA
    language: str = "en"
    download_dir: Path = Path("models/whisper")


class FramingConfig(BaseModel):
    """Her own lines around a brief readout (see secretary/framing.py)."""

    enabled: bool = True
    # Most reactions between paragraphs (0 = opener and closer only).
    asides: int = Field(default=2, ge=0, le=5)
    # Longest she waits for the model, counted from the start of the readout.
    # A loaded gemma4:12b writes the opener in well under a second; a cold one
    # takes ~20 s to load, so she skips the framing that time.
    wait_seconds: float = Field(default=3.0, ge=0.0, le=30.0)
    max_words: int = Field(default=18, ge=4, le=40)


class AppConfig(BaseModel):
    secretary_dir: Path = Field(description="Synced folder holding briefs/ and escalations/")
    state_file: Path = Path("data/state.json")
    persona_file: Path = Path("personas/juniper.yaml")
    poll_seconds: float = 5.0
    history_turns: int = 12
    host: str = "127.0.0.1"
    port: int = 8765
    ollama: OllamaConfig = OllamaConfig()
    tts: TtsConfig = TtsConfig()
    stt: SttConfig = SttConfig()
    brief_framing: FramingConfig = FramingConfig()

    @property
    def briefs_dir(self) -> Path:
        return self.secretary_dir / "briefs"

    @property
    def escalations_dir(self) -> Path:
        return self.secretary_dir / "escalations"


def load_config(path: Path) -> AppConfig:
    """Load config.yaml. Relative paths resolve against the config file's folder."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    config = AppConfig.model_validate(raw)
    base = path.resolve().parent

    def resolve(p: Path) -> Path:
        return p if p.is_absolute() else (base / p)

    config.secretary_dir = resolve(config.secretary_dir)
    config.state_file = resolve(config.state_file)
    config.persona_file = resolve(config.persona_file)
    config.tts.model_path = resolve(config.tts.model_path)
    config.tts.voices_path = resolve(config.tts.voices_path)
    config.stt.download_dir = resolve(config.stt.download_dir)
    return config
