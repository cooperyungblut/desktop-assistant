"""Write questions for Claude, and mark them answered (see docs/formats.md)."""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

from . import frontmatter
from .fileutil import atomic_write_text

SCHEMA = "secretary-escalation/1"

# Deliberately explicit: the app escalates only when you clearly ask for it,
# never because the local model decided to. Small models over-trigger.
_ESCALATE = re.compile(
    r"^\s*/claude\b"  # slash command: "/claude did Dana reply?"
    r"|\b(check|ask|confirm|verify)\s+(this\s+|that\s+)?with\s+claude\b"
    r"|\b(ask|tell|escalate\s+to)\s+claude\b",
    re.IGNORECASE,
)


class Status(StrEnum):
    OPEN = "open"
    ANSWERED = "answered"


class Escalation(BaseModel):
    id: str
    created_at: dt.datetime
    status: Status = Status.OPEN
    about_brief: str | None = None
    answered_in: str | None = None
    question: str
    context: list[tuple[str, str]] = []  # (speaker, text)


def wants_escalation(user_text: str) -> bool:
    return bool(_ESCALATE.search(user_text))


def strip_command(user_text: str) -> str:
    """'/claude did Dana reply?' -> 'did Dana reply?'"""
    return re.sub(r"^\s*/claude\b[:,]?\s*", "", user_text, flags=re.IGNORECASE).strip()


def new_escalation(
    question: str,
    *,
    about_brief: str | None,
    context: Iterable[tuple[str, str]] = (),
    now: dt.datetime | None = None,
) -> Escalation:
    now = now or dt.datetime.now().astimezone()
    return Escalation(
        id=f"esc-{now:%Y%m%d-%H%M%S}",
        created_at=now.replace(microsecond=0),
        about_brief=about_brief,
        question=question.strip(),
        context=list(context),
    )


def render(esc: Escalation) -> str:
    meta = {
        "schema": SCHEMA,
        "id": esc.id,
        "created_at": esc.created_at.isoformat(),
        "status": esc.status.value,
        "about_brief": esc.about_brief,
        "answered_in": esc.answered_in,
    }
    body = f"# Question\n\n{esc.question}\n"
    if esc.context:
        lines = "\n".join(f"- **{who}:** {_one_line(text)}" for who, text in esc.context)
        body += f"\n# Context\n\nLast few turns of the conversation, for reference:\n\n{lines}\n"
    return frontmatter.join(meta, body)


def parse(text: str) -> Escalation:
    meta, body = frontmatter.split(text)
    if meta.get("schema") != SCHEMA:
        raise ValueError(f"unsupported schema {meta.get('schema')!r}")
    question = _section(body, "Question")
    context = [
        (m.group(1), m.group(2))
        for m in re.finditer(r"^- \*\*(.+?):\*\* (.*)$", _section(body, "Context"), re.MULTILINE)
    ]
    return Escalation(
        id=meta["id"],
        created_at=meta["created_at"],
        status=meta.get("status", "open"),
        about_brief=meta.get("about_brief"),
        answered_in=meta.get("answered_in"),
        question=question,
        context=context,
    )


def write(esc: Escalation, directory: Path) -> Path:
    path = directory / f"{esc.id}.md"
    atomic_write_text(path, render(esc))
    return path


def mark_answered(directory: Path, ids: Iterable[str], brief_id: str) -> list[str]:
    """Flip listed escalations to answered. Returns the ids actually changed."""
    changed = []
    for esc_id in ids:
        path = directory / f"{esc_id}.md"
        if not path.is_file():
            continue  # unknown id, or written on another machine: ignore
        esc = parse(path.read_text(encoding="utf-8"))
        if esc.status is Status.ANSWERED:
            continue
        esc.status = Status.ANSWERED
        esc.answered_in = brief_id
        write(esc, directory)
        changed.append(esc_id)
    return changed


def _section(body: str, name: str) -> str:
    m = re.search(rf"^#\s+{name}\s*$(.*?)(?=^#\s|\Z)", body, re.MULTILINE | re.DOTALL)
    return m.group(1).strip() if m else ""


def _one_line(text: str) -> str:
    return " ".join(text.split())
