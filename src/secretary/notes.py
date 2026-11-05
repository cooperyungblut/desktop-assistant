"""Notes she writes down for you: "write that down: the safe code is 4471".

Notes are plain Markdown files in a local folder (`notes.dir`), one file per
page. She appends one line per note and flushes it straight away, so an editor
that reloads changed files (Notepad++, see docs/notes.md) shows it live, for
example on a side monitor.

Like brief and calendar requests this is a narrow pattern match in code, and
the note is written exactly as heard: the model never rewrites it.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .config import NotesConfig

log = logging.getLogger(__name__)

_FILLER = r"(?:(?:hey|okay|ok|so|um|uh|alright|and)\b[\s,]*)*"
_POLITE = r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|please\s+|go\s+ahead\s+and\s+)?"
_PUNCT = r"[\s:,.;!?\-–—]*"
_END = r"(?=[\s:,.;!?\-–—]|$)"

# "write that down: X", "jot down X", "take a note, X", "make a note that X",
# "add to my notes X", "note: X"
_LEADING = re.compile(
    rf"^\s*{_FILLER}{_POLITE}"
    r"(?:"
    r"(?:write|jot|note)\s+(?:(?:that|this|it)\s+)?down(?:\s+for\s+me)?"
    r"|(?:take|make|add)\s+(?:a\s+)?(?:new\s+)?note(?:\s+of\s+(?:that|this))?(?:\s+for\s+me)?"
    r"|add\s+(?:(?:that|this|it)\s+)?to\s+(?:my|the)\s+notes"
    r"|(?:a\s+)?new\s+note\s*:"
    r"|note\s*:"
    r"|note\s+that"
    r")"
    r"(?:\s+please)?"
    rf"{_END}{_PUNCT}(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)
# "The code is 4471, write that down."
_TRAILING = re.compile(
    r"^\s*(?P<rest>.+?)[\s,.;:\-–—]+"
    rf"{_POLITE}(?:write|jot|note)\s+(?:that|this|it)\s+down"
    r"(?:\s+for\s+me)?(?:\s+please)?[\s.!?]*$",
    re.IGNORECASE | re.DOTALL,
)
# "start a new page called Escape room", "new notes for the lighthouse puzzle"
_NEW_PAGE = re.compile(
    rf"^\s*{_FILLER}{_POLITE}"
    r"(?:(?:start|make|create|open|begin)\s+)?(?:a\s+)?new\s+"
    r"(?:notes?\s+(?:page|file)|page\s+of\s+notes|notes|page|notepad)"
    r"(?:\s+(?:called|named|titled|for|about))?"
    rf"{_END}{_PUNCT}(?P<name>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_READ = re.compile(
    rf"^\s*{_FILLER}{_POLITE}"
    r"(?:"
    r"(?:read|tell)\s+(?:me\s+)?(?:back\s+)?(?:my|the)\s+(?:last\s+)?notes?(?:\s+back)?"
    r"|what(?:'s|’s|\s+is|\s+was)\s+in\s+(?:my|the)\s+notes"
    r"|what\s+did\s+(?:i|you)\s+(?:write|jot|note)\s+down"
    r")\b",
    re.IGNORECASE,
)
_OPEN = re.compile(
    rf"^\s*{_FILLER}{_POLITE}(?:open|show)\s+(?:me\s+)?(?:my|the)\s+notes?\b[\s.!?]*$",
    re.IGNORECASE,
)
_CANCEL = re.compile(
    r"^\s*(?:never\s*mind|nevermind|cancel|forget\s+it|nothing|no)\b[\s.!?,]*\w*[\s.!?]*$",
    re.IGNORECASE,
)
_LEADING_THAT = re.compile(r"^(?:that|this)\s+(?=\S)", re.IGNORECASE)
_BAD_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(frozen=True)
class NoteRequest:
    kind: Literal["write", "new_page", "read", "open"]
    text: str = ""  # the note, or the new page's name ("" = ask / use a dated name)


def clean_note(text: str) -> str:
    """Trim what was heard: spaces, a leading "that", one final period."""
    text = " ".join(text.split())
    text = _LEADING_THAT.sub("", text)
    if text.endswith(".") and not text.endswith(".."):
        text = text[:-1].rstrip()
    return text


def note_request(text: str, name: str | None = None) -> NoteRequest | None:
    """`NoteRequest` if the text asks her to take, start, read or open notes.
    `name` is hers, so "Juniper, write that down: ..." works too."""
    if name:
        text = re.sub(rf"^\s*(?:hey\s+)?{re.escape(name)}\b[\s,.!:]*", "", text, flags=re.I)
    if _OPEN.match(text):
        return NoteRequest("open")
    if _READ.match(text):
        return NoteRequest("read")
    if m := _NEW_PAGE.match(text):
        return NoteRequest("new_page", clean_note(m["name"]).rstrip("?!"))
    if m := _LEADING.match(text):
        return NoteRequest("write", clean_note(m["rest"]))
    if m := _TRAILING.match(text):
        return NoteRequest("write", clean_note(m["rest"]))
    return None


def is_cancel(text: str) -> bool:
    return bool(_CANCEL.match(text))


def page_file_name(name: str) -> str:
    """A safe Windows file name for a page: "Escape room" -> "Escape room.md"."""
    name = _BAD_NAME_CHARS.sub(" ", name)
    name = " ".join(name.split()).strip(" .")[:80].rstrip(" .")
    return f"{name or 'Notes'}.md"


class Notebook:
    """The notes folder. The current page is the most recently written one, so
    it survives restarts without any extra state."""

    def __init__(self, config: NotesConfig, now=dt.datetime.now):
        self._config = config
        self._now = now
        self._current: Path | None = None

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    @property
    def dir(self) -> Path:
        return self._config.dir

    def current_page(self) -> Path:
        if self._current is None:
            pages = list(self.dir.glob("*.md")) if self.dir.is_dir() else []
            if pages:
                self._current = max(pages, key=lambda p: p.stat().st_mtime)
            else:
                self._current = self.dir / page_file_name(self._config.default_page)
        return self._current

    def new_page(self, name: str) -> Path:
        """Start a page (or switch back to one that already exists) and make it current."""
        if not name:
            name = f"Notes {self._now():%Y-%m-%d %H%M}"
        path = self.dir / page_file_name(name)
        if not path.exists():
            self._create(path)
        self._current = path
        self.open(path)
        return path

    def write(self, note: str) -> Path:
        """Append one note to the current page and flush it to disk."""
        path = self.current_page()
        fresh = not path.exists()
        if fresh:
            self._create(path)
        stamp = f"[{self._now():%H:%M}] " if self._config.timestamps else ""
        with path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(f"- {stamp}{note}\n")
            f.flush()
            os.fsync(f.fileno())
        log.info("Wrote a note to %s", path.name)
        if fresh:
            self.open(path)
        return path

    def last_notes(self, count: int = 5) -> list[str]:
        """The newest notes on the current page, oldest first, without the "- [12:34] "."""
        path = self.current_page()
        if not path.is_file():
            return []
        notes = [
            re.sub(r"^-\s+(?:\[\d{1,2}:\d{2}\]\s+)?", "", line).strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith("- ")
        ]
        return notes[-count:]

    def open(self, path: Path | None = None) -> bool:
        """Show a page in the viewer from `notes.open_with`. False if none is set."""
        path = path or self.current_page()
        viewer = self._config.open_with.strip()
        if not viewer:
            return False
        if not path.exists():
            self._create(path)
        try:
            if viewer.lower() == "default":
                if sys.platform == "win32":
                    os.startfile(path)  # type: ignore[attr-defined]
                else:
                    opener = "open" if sys.platform == "darwin" else "xdg-open"
                    subprocess.Popen([opener, str(path)])
            else:
                subprocess.Popen([viewer, str(path)])
        except OSError:
            log.exception("Couldn't open %s with %s", path, viewer)
            return False
        return True

    def _create(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as f:
            f.write(f"# {path.stem}\n\n")
