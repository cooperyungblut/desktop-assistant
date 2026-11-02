"""Load the persona file (personas/*.yaml)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from .expressions import EMOTIONS


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
    # Silence after each spoken sentence, in seconds. Speech is made one sentence
    # at a time, and Kokoro trims each clip, so without this they run together.
    sentence_pause: float = Field(default=0.35, ge=0.0, le=2.0)
    # Silence after a comma, semicolon, colon or dash.
    clause_pause: float = Field(default=0.15, ge=0.0, le=2.0)
    # Make each clause separately so every comma gets clause_pause. Off means
    # Kokoro picks comma pauses itself, which vary a lot.
    split_clauses: bool = True


# VRM 1.0 preset names. three-vrm maps VRM 0.x names (joy, sorrow, fun) onto
# these, but VRM 0.x models have no "surprised" at all.
VRM_PRESETS = ("neutral", "happy", "angry", "sad", "relaxed", "surprised")


class Expression(BaseModel):
    """How one mood looks: a VRM expression preset and how strongly to apply it."""

    preset: str = "neutral"
    weight: float = Field(default=0.6, ge=0.0, le=1.0)
    # Strength while she's speaking. Many smiles open the mouth, which fights
    # with lip sync, so the default is half of `weight`.
    talk_weight: float | None = Field(default=None, ge=0.0, le=1.0)

    @property
    def talking(self) -> float:
        return self.weight / 2 if self.talk_weight is None else self.talk_weight

    @field_validator("preset")
    @classmethod
    def _known_preset(cls, v: str) -> str:
        if v not in VRM_PRESETS:
            raise ValueError(f"preset must be one of {', '.join(VRM_PRESETS)}")
        return v


DEFAULT_EXPRESSIONS: dict[str, Expression] = {
    "neutral": Expression(preset="neutral", weight=0.0),
    "happy": Expression(preset="happy", weight=0.6),
    "surprised": Expression(preset="surprised", weight=0.5),
    "sad": Expression(preset="sad", weight=0.45),
    "relaxed": Expression(preset="relaxed", weight=0.5),
}


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
    expressions: dict[str, Expression] = Field(default_factory=dict, validate_default=True)

    @field_validator("expressions")
    @classmethod
    def _fill_expressions(cls, v: dict[str, Expression]) -> dict[str, Expression]:
        unknown = set(v) - set(EMOTIONS)
        if unknown:
            raise ValueError(
                f"unknown mood(s) {', '.join(sorted(unknown))}; "
                f"the moods are fixed: {', '.join(EMOTIONS)}"
            )
        return {**DEFAULT_EXPRESSIONS, **v}

    def expression(self, mood: str | None) -> Expression:
        """The look for a mood. No tag, or an unknown one, means neutral."""
        return self.expressions.get(mood or "neutral", self.expressions["neutral"])


def load_persona(path: Path) -> Persona:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PersonaError(f"can't read persona file {path}: {exc}") from exc
    try:
        return Persona.model_validate(raw)
    except ValidationError as exc:
        raise PersonaError(f"invalid persona file {path.name}:\n{exc}") from exc
