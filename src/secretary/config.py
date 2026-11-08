"""Local app configuration (paths, ports, model names).

The real config lives in `config.yaml`, which is git-ignored because it holds
personal paths such as the Google Drive folder. `config.example.yaml` is the
committed template.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import AliasChoices, BaseModel, Field


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


class CalendarConfig(BaseModel):
    """Adding events to a calendar by voice or chat (see secretary/google_calendar.py)."""

    engine: str = "none"  # "google" or "none"
    calendar_id: str = "primary"
    # The OAuth client you download from Google Cloud, and the saved sign-in.
    # Both are personal: keep them under data/, which is git-ignored.
    client_file: Path = Path("data/google-oauth-client.json")
    token_file: Path = Path("data/google-token.json")
    # Length of an event when you only say when it starts.
    default_minutes: int = Field(default=60, ge=5, le=24 * 60)
    # How long her "Should I add it?" waits for a yes.
    confirm_seconds: float = Field(default=300.0, ge=10.0)


class WikiConfig(BaseModel):
    """Offline Wikipedia lookup (see secretary/wiki.py and docs/wikipedia.md)."""

    engine: str = "none"  # "local" once you've built the index
    # What the index is built from (only needed to build it): a Kiwix .zim file,
    # or a folder of Hugging Face parquet files. `source_dir` still works.
    source: Path = Field(
        default=Path("data/wikipedia.zim"),
        validation_alias=AliasChoices("source", "source_dir"),
    )
    # CPU processes used to convert a ZIM's HTML while building.
    workers: int = Field(default=4, ge=1, le=64)
    # The search index built from them. Keep both outside the repo if you like.
    index_file: Path = Path("data/wikipedia.sqlite")
    # How much of each article goes into the index: the opening plus the first
    # sections. Raise it for deeper answers (bigger, slower to build).
    max_chars_per_article: int = Field(default=24_000, ge=500)
    # A lookup that takes longer than this is skipped, and she answers without it.
    timeout_seconds: float = Field(default=15.0, ge=0.5, le=60.0)
    # A lookup still running after this long gets a short "one moment" line first.
    notice_after_seconds: float = Field(default=1.5, ge=0.0, le=60.0)
    # Whether she says "Wikipedia says..." with a looked-up fact. Handy while
    # checking her lookups; turn off to have her say it as if she knew it.
    mention_source: bool = True
    # Folder with a cross-encoder (model.onnx + tokenizer.json) that re-orders the
    # search's passages by how well they answer the question. Needs the rerank
    # extra; leave unset for keyword ranking only (docs/wikipedia.md).
    rerank_model: Path | None = None
    # How its scores are used: "keep" (the keyword search's top two, then its
    # order; best in the eval), "fuse" (blend with the keyword order) or "rerank"
    # (its scores alone).
    rerank_mode: Literal["fuse", "rerank", "keep"] = "keep"
    # Read the index into the OS cache at startup (in the background) so the first
    # question isn't slow.
    warm_up: bool = True


class NotesConfig(BaseModel):
    """Notes she writes down for you (see secretary/notes.py and docs/notes.md)."""

    enabled: bool = True
    # One Markdown file per page. Keep it out of the repo (data/ is git-ignored);
    # a synced folder works too if you want the notes on your phone.
    dir: Path = Path("data/notes")
    # The page used until you start another one.
    default_page: str = "Notes"
    timestamps: bool = True  # "- [17:21] the safe code is 4471"
    # What shows a page when she starts one, or when you say "open my notes":
    # an editor's path (Notepad++ reloads changed files live), "default" for the
    # system's app for .md files, or "" to never open anything.
    open_with: str = ""


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
    calendar: CalendarConfig = CalendarConfig()
    wiki: WikiConfig = WikiConfig()
    notes: NotesConfig = NotesConfig()

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
    config.calendar.client_file = resolve(config.calendar.client_file)
    config.calendar.token_file = resolve(config.calendar.token_file)
    config.wiki.source = resolve(config.wiki.source)
    config.wiki.index_file = resolve(config.wiki.index_file)
    config.notes.dir = resolve(config.notes.dir)
    return config
