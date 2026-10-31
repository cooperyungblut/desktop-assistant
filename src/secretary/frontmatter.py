"""Minimal YAML front-matter reader/writer shared by briefs and escalations."""

from __future__ import annotations

from typing import Any

import yaml


class FrontMatterError(ValueError):
    """The file doesn't start with a valid `---` YAML block."""


def split(text: str) -> tuple[dict[str, Any], str]:
    """Return (metadata, body) for a document that starts with a `---` block."""
    text = text.lstrip("﻿")  # tolerate a UTF-8 BOM from Windows editors
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise FrontMatterError("missing opening '---' line")
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            meta = yaml.safe_load("\n".join(lines[1:i])) or {}
            if not isinstance(meta, dict):
                raise FrontMatterError("front matter is not a mapping")
            return meta, "\n".join(lines[i + 1 :])
    raise FrontMatterError("missing closing '---' line")


def join(meta: dict[str, Any], body: str) -> str:
    header = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{header}\n---\n\n{body.strip()}\n"
