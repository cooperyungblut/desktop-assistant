"""Load the persona file (personas/*.yaml)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError


class PersonaError(ValueError):
    pass


class Personality(BaseModel):
    traits: list[str] = Field(min_length=1)
    speaking_style: str
    answer_length: str = "one to three short sentences"


class Lines(BaseModel):
    escalated: str = "Okay, I've passed that to Claude. The answer will be in your next brief."
    no_brief: str = "I don't have a brief loaded yet."
    model_unavailable: str = "Sorry, the local model isn't responding. Is Ollama running?"


class Voice(BaseModel):
    kokoro_voice: str = "af_heart"
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    lang: str = "en-us"


class Sampling(BaseModel):
    """Ollama options. Unknown keys (e.g. top_k, seed) are passed through."""

    model_config = {"extra": "allow"}

    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_p: float = Field(default=0.9, gt=0.0, le=1.0)
    repeat_penalty: float = 1.1
    num_ctx: int = Field(default=8192, ge=2048)

    def as_options(self) -> dict[str, Any]:
        return self.model_dump()


class Persona(BaseModel):
    name: str
    user_name: str = "the user"
    role: str = "personal secretary"
    personality: Personality
    example_lines: list[str] = []
    lines: Lines = Lines()
    voice: Voice = Voice()
    sampling: Sampling = Sampling()


def load_persona(path: Path) -> Persona:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PersonaError(f"can't read persona file {path}: {exc}") from exc
    try:
        return Persona.model_validate(raw)
    except ValidationError as exc:
        raise PersonaError(f"invalid persona file {path.name}:\n{exc}") from exc
