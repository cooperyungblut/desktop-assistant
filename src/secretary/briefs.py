"""Parse and validate brief files (see docs/formats.md)."""

from __future__ import annotations

import datetime as dt
import re
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, field_validator

from . import frontmatter

SCHEMA = "secretary-brief/1"
_SECTION = re.compile(r"^#\s+(Display|Spoken)\s*$", re.MULTILINE | re.IGNORECASE)


class BriefError(ValueError):
    """A brief file is missing, incomplete or doesn't follow the format."""


class BriefType(StrEnum):
    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"


class Brief(BaseModel):
    id: str
    type: BriefType
    date: dt.date
    generated_at: dt.datetime
    answers_escalations: list[str] = Field(default_factory=list)
    sample: bool = False  # fake data for development and demos
    display: str
    spoken: str

    @field_validator("generated_at")
    @classmethod
    def _assume_local_time(cls, value: dt.datetime) -> dt.datetime:
        # A missing UTC offset would make naive and aware times impossible to compare.
        return value if value.tzinfo else value.astimezone()


def parse_brief(text: str, *, expected_id: str | None = None) -> Brief:
    try:
        meta, body = frontmatter.split(text)
    except frontmatter.FrontMatterError as exc:
        raise BriefError(str(exc)) from exc

    if meta.get("schema") != SCHEMA:
        raise BriefError(f"unsupported schema {meta.get('schema')!r}, expected {SCHEMA!r}")

    sections = _split_sections(body)
    try:
        brief = Brief(
            id=str(meta.get("id", "")),
            type=meta.get("type"),
            date=meta.get("date"),
            generated_at=meta.get("generated_at"),
            answers_escalations=meta.get("answers_escalations") or [],
            sample=bool(meta.get("sample", False)),
            display=sections["display"],
            spoken=sections["spoken"],
        )
    except ValidationError as exc:
        raise BriefError(f"invalid brief: {exc}") from exc

    if not brief.id:
        raise BriefError("missing id")
    if expected_id is not None and brief.id != expected_id:
        raise BriefError(f"id {brief.id!r} doesn't match file name {expected_id!r}")
    return brief


def load_brief(path: Path) -> Brief:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise BriefError(f"can't read {path.name}: {exc}") from exc
    return parse_brief(text, expected_id=path.stem)


def _split_sections(body: str) -> dict[str, str]:
    matches = list(_SECTION.finditer(body))
    names = [m.group(1).lower() for m in matches]
    if names != ["display", "spoken"]:
        raise BriefError(
            "body must contain '# Display' then '# Spoken' (exactly once each), "
            f"found {names or 'neither'}"
        )
    display = body[matches[0].end() : matches[1].start()].strip()
    spoken = body[matches[1].end() :].strip()
    if not spoken:
        raise BriefError("the '# Spoken' section is empty (the file may still be syncing)")
    return {"display": display, "spoken": spoken}
