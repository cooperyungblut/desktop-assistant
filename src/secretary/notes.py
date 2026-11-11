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
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .config import NotesConfig
from .viewer_prompts import dismiss_in_background

log = logging.getLogger(__name__)

_FILLER = r"(?:(?:hey|okay|ok|so|um|uh|alright|and)\b[\s,]*)*"
_POLITE = (
    r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|please\s+|go\s+ahead\s+and\s+"
    r"|(?:i(?:'ll|’ll|\s+will)|let(?:'s|’s|s)|we(?:'ll|’ll|\s+will)|can\s+we|could\s+we"
    r"|i\s+want\s+to|i'd\s+like\s+to)\s+)?"
)
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
# It needs punctuation before the command, or a "can you", so a question like
# "where did you write that down?" never ends up as a note.
_TRAILING = re.compile(
    r"^\s*(?P<rest>.+?)(?:\s*[,.;:\-–—]\s*|\s+(?=(?:can|could|would|will|please)\b))"
    rf"{_POLITE}(?:write|jot|note)\s+(?:that|this|it)\s+down"
    r"(?:\s+for\s+me)?(?:\s+please)?[\s.!?]*$",
    re.IGNORECASE | re.DOTALL,
)
# "start a new page called Escape room", "new notes for the lighthouse puzzle"
_NEW_PAGE = re.compile(
    rf"^\s*{_FILLER}{_POLITE}"
    r"(?:(?:start|make|create|open|begin)\s+)?(?:a\s+)?new\s+"
    r"(?:notes?\s+(?:page|file)|page\s+of\s+notes|notes|page|notepad)"
    r"(?:[\s,.:;]+(?:called|call|named|titled|for|about))?"
    rf"{_END}{_PUNCT}(?P<name>.*)$",
    re.IGNORECASE | re.DOTALL,
)
# "rename these notes Haunted House", "call this page Escape room"
_RENAME = re.compile(
    rf"^\s*{_FILLER}{_POLITE}"
    r"(?:(?:we(?:'ll|’ll|\s+will)|let(?:'s|’s|s)?(?:\s+us)?|i\s+want\s+to)\s+)?"
    r"(?:rename|retitle|call|name|title)\s+(?:these|this|the|my|our)\s+"
    r"(?:(?:current|open|active)\s+)?"
    r"(?:notes?(?:\s+(?:page|file))?|page|file|notepad)"
    r"(?:[\s,:]+(?:to|as))?[\s,:]+(?P<name>.+)$",
    re.IGNORECASE | re.DOTALL,
)
# "open a page called Test5", "switch to the page called Escape room"
_NAMED_PAGE = re.compile(
    rf"^\s*{_FILLER}{_POLITE}"
    r"(?:"
    r"(?:start|make|create|open|begin|show\s+me)\s+(?:a\s+|the\s+|my\s+)?"
    r"(?:(?:notes?\s+)?(?:page|file|notepad)|notes?)"
    r"[\s,.:;]+(?:called|call|named|titled|for)"
    r"|(?:switch|go\s+back|go|change)\s+to\s+(?:the\s+)?(?:notes?\s+)?page"
    r"(?:[\s,]+(?:called|call|named|titled))?"
    r"|(?:open|show\s+me|pull\s+up|bring\s+up)\s+(?:the\s+|my\s+)?"
    r"(?:notes?\s+(?:page|file)|page|notes)"
    r")"
    rf"{_END}{_PUNCT}(?P<name>.+)$",
    re.IGNORECASE | re.DOTALL,
)
# "open the latest notes", "pull up my last page"
_LATEST = re.compile(
    rf"^\s*{_FILLER}{_POLITE}"
    r"(?:open|show|pull\s+up|bring\s+up|go\s+to|switch\s+to|go\s+back\s+to)\s+(?:me\s+)?"
    r"(?:my\s+|the\s+)?(?:latest|last|newest|most\s+recent|recent)\s+"
    r"(?:notes?(?:\s+(?:page|file))?|page|file|notepad)\b[\s.!?]*$",
    re.IGNORECASE,
)
# "open the one before that", "go back to the previous page", "go back a page"
_PREVIOUS = re.compile(
    rf"^\s*{_FILLER}{_POLITE}"
    r"(?:(?:open|show|pull\s+up|bring\s+up|go\s+to|switch\s+to|go\s+back\s+to|go\s+back)"
    r"\s+(?:me\s+)?(?:to\s+)?)?"
    r"(?:the\s+|my\s+)?"
    r"(?:(?:one|page|notes?(?:\s+page)?)\s+before\s+(?:that|this|it)"
    r"|previous\s+(?:one|page|notes?(?:\s+(?:page|file))?)"
    r"|(?:(?:go|step|move)\s+)?back\s+(?:a|one)\s+(?:notes?\s+)?page)"
    r"\b[\s.!?]*$",
    re.IGNORECASE,
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
# "Where did you write that down?", "which page are my notes on?"
_WHERE = re.compile(
    r"\b(?:where|which\s+(?:page|file)|what\s+(?:page|file))"
    r"(?:\s+[\w'’]+){0,4}?\s+"
    r"(?:write|wrote|written|put|save|saved|jot|jotted|note|noted|notes)\b",
    re.IGNORECASE,
)
# "cross off the vault notes", "mark the vault code as used", "archive the gate code"
_USED = re.compile(
    rf"^\s*{_FILLER}{_POLITE}"
    r"(?:"
    r"(?:mark|set)\s+(?P<a>.+?)\s+(?:as\s+)?(?:used|done|finished|completed?)"
    r"|(?:cross|strike|scratch)\s+(?:off|out|through)\s+(?P<b>.+?)"
    r"|(?:cross|strike|scratch)\s+(?P<c>.+?)\s+(?:off|out|through)"
    r"|archive\s+(?P<d>.+?)"
    r")[\s.!?]*$",
    re.IGNORECASE | re.DOTALL,
)
_OPEN = re.compile(
    rf"^\s*{_FILLER}{_POLITE}(?:open|show)\s+(?:me\s+)?(?:(?:my|the)\s+)?notes?\b[\s.!?]*$",
    re.IGNORECASE,
)
_CANCEL = re.compile(
    r"^\s*(?:never\s*mind|nevermind|cancel|forget\s+it|nothing|no)\b[\s.!?,]*\w*[\s.!?]*$",
    re.IGNORECASE,
)
_TRAILING_POLITE = re.compile(
    r"[\s,]+(?:please|thanks|thank\s+you|for\s+me)[\s.!?]*$", re.IGNORECASE
)
# "open Test Monitor on": only used when it names a page that already exists.
_OPEN_ANY = re.compile(
    rf"^\s*{_FILLER}{_POLITE}"
    r"(?:open|show\s+me|pull\s+up|bring\s+up|switch\s+to|go\s+(?:back\s+)?to)[\s,:]+"
    r"(?:up\s+)?(?:the\s+|my\s+)?(?P<name>.+?)(?:\s+(?:page|notes?))?[\s.!?]*$",
    re.IGNORECASE | re.DOTALL,
)
_NOT_A_NAME = {"please", "now", "again", "up", "for me", "back up"}
_LEADING_THAT = re.compile(r"^(?:that|this)\s+(?=\S)", re.IGNORECASE)
_BAD_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(frozen=True)
class NoteRequest:
    kind: Literal[
        "write", "new_page", "read", "open", "latest", "previous", "where", "used", "rename"
    ]
    text: str = ""  # the note, or the new page's name ("" = ask / use a dated name)


def clean_note(text: str) -> str:
    """Trim what was heard: spaces, a leading "that", one final period."""
    text = " ".join(text.split())
    text = _LEADING_THAT.sub("", text)
    if text.endswith(".") and not text.endswith(".."):
        text = text[:-1].rstrip()
    return text


_ASKED = re.compile(r"^\s*(?:hey|so|okay|ok)?[\s,]*(?:can|could|would|will)\s+you\b", re.IGNORECASE)


def note_request(text: str, name: str | None = None) -> NoteRequest | None:
    """`NoteRequest` if the text asks her to take, start, read or open notes.
    `name` is hers, so "Juniper, write that down: ..." works too."""
    if name:
        text = re.sub(rf"^\s*(?:hey\s+)?{re.escape(name)}\b[\s,.!:]*", "", text, flags=re.I)
    # Commands may end in "please"; notes keep every word, so write uses `text`.
    command = _TRAILING_POLITE.sub("", text)
    if _OPEN.match(command):
        return NoteRequest("open")
    if _LATEST.match(command):
        return NoteRequest("latest")
    if _PREVIOUS.match(command):
        return NoteRequest("previous")
    if _READ.match(command):
        return NoteRequest("read")
    if m := _USED.match(command):
        what = m["a"] or m["b"] or m["c"] or m["d"]
        if _THAT.match(what):
            return NoteRequest("used")
        topic = topic_words(what)
        return NoteRequest("used", " ".join(w for w in _words(what) if w in topic))
    if m := _RENAME.match(command):
        # Not clean_note: a page can be called "This One".
        return NoteRequest("rename", m["name"].strip().rstrip(".?!").strip())
    if m := _NEW_PAGE.match(command) or _NAMED_PAGE.match(command):
        name = clean_note(m["name"]).rstrip("?!")
        if name.lower().strip(" .,!") in _NOT_A_NAME:  # "open my notes please"
            return NoteRequest("open")
        return NoteRequest("new_page", name)
    if m := _LEADING.match(text):
        note = clean_note(m["rest"])
        if _ASKED.match(text):  # "can you write down that ...?": the "?" is the asking
            note = clean_note(note.rstrip("?").rstrip())
        return NoteRequest("write", note)
    if _WHERE.search(text):
        return NoteRequest("where")
    if m := _TRAILING.match(text):
        return NoteRequest("write", clean_note(m["rest"]))
    return None


# "open Test 4 and write down jelly beans": a page command, then a note.
_THEN_WRITE = re.compile(
    r"^(?P<first>.+?)[\s,]+(?:and\s+then|and|then)[\s,]+"
    r"(?P<second>(?:write|jot|note|take\s+a\s+note|make\s+a\s+note|add)\b.*)$",
    re.IGNORECASE | re.DOTALL,
)


_THAT = re.compile(r"^\s*(?:that|this|it|them|those)\s*$", re.IGNORECASE)
_STOP_WORDS = {
    "the", "a", "an", "my", "our", "all", "of", "for", "to", "in", "on", "and",
    "that", "this", "these", "those", "it", "note", "notes", "page", "line", "lines",
    "is", "was", "what", "whats", "s", "me", "tell", "i", "you", "from", "with", "using",
    "about", "as", "used", "done", "who", "where", "when", "why", "how", "which", "did",
    "do", "does", "are", "were", "be", "can", "could", "would", "will", "there", "has",
    "have", "had", "been", "check", "find", "look", "up", "your", "previous", "last", "old",
    "older", "other", "pages",
}  # fmt: skip
# Words that say what kind of thing it is, not which one: "vault code" is about the vault.
_KIND_WORDS = {"code", "codes", "password", "passwords", "pin", "combination", "number",
               "numbers", "digit", "digits", "combo"}  # fmt: skip


def _words(text: str) -> list[str]:
    """Lowercase words, splitting VaultDigit2 into vault, digit, 2."""
    text = re.sub(r"(?<=[a-z])(?=[A-Z0-9])|(?<=[0-9])(?=[A-Za-z])", " ", text)
    return re.findall(r"[a-z0-9]+", text.lower())


def topic_words(text: str) -> set[str]:
    """The words that pick out which notes: "the vault code" -> {"vault"}."""
    words = {w for w in _words(text) if w not in _STOP_WORDS}
    return (words - _KIND_WORDS) or words


def asks_about_notes(question: str) -> bool:
    """A question for a code, password or the like, or one that mentions notes."""
    return bool(set(_words(question)) & (_KIND_WORDS | {"note", "notes", "wrote", "written"}))


def asks_for_a_code(question: str, notes: Sequence[str]) -> bool:
    """True for "what's the gate code?" when a note mentions the gate, so the
    answer comes from the notes without a Wikipedia lookup first."""
    words = set(_words(question))
    if not words & _KIND_WORDS:
        return False
    topic = topic_words(question) - _KIND_WORDS
    return bool(topic) and any(topic & set(_words(n)) for n in notes)


# "write down that vault code", "write down the code from the notes": the note
# points at something instead of saying it. Notes with digits are dictated.
_REFERS = re.compile(
    r"\b(?:write|jot)\s+(?:it\s+)?down\s+(?:that|this|those|the)(?:\s+[\w'’]+){1,3}[\s.!?]*$"
    r"|\b(?:from|in|on|using|based\s+on)\s+(?:the|my|your)\s+notes\b"
    r"|\busing\s+the\b"
    r"|\byou\s+(?:just\s+)?(?:said|found|told|worked\s+out|figured\s+out)\b"
    r"|\b(?:your|that|the)\s+answer\b",
    re.IGNORECASE,
)


def refers_to_something(text: str) -> bool:
    return not re.search(r"\d", text) and bool(_REFERS.search(text))


def filled_note(text: str) -> str:
    """The model's note, tidied: one line, no quotes or "Note:" label; "" if
    it couldn't tell what to write."""
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    line = re.sub(r"^(?:note\s*:\s*)", "", line, flags=re.IGNORECASE).strip(" \"'“”‘’*")
    if not line or "UNKNOWN" in line.upper():
        return ""
    return clean_note(line)[:200]


_GOES_TO = re.compile(r"^\W*(?:\w+\W+){0,4}?(?:switch|go(?:\s+back)?|change)\s+to\b", re.I)


def only_existing(text: str) -> bool:
    """ "Go to / switch to the page X" moves to a page that exists; it never
    starts one ("go to the page we started with" is not a page name)."""
    return bool(_GOES_TO.match(text))


def split_then_write(text: str) -> tuple[str, str] | None:
    """("open Test 4", "write down jelly beans") for a two-step request, else None.
    The caller checks that the first part is a page command."""
    m = _THEN_WRITE.match(text)
    if m is None or not _LEADING.match(m["second"]):
        return None
    return m["first"], m["second"]


def open_page_name(text: str, name: str | None = None) -> str | None:
    """The page name in a bare "open X" / "switch to X". The caller checks that
    such a page exists, so "open the door" never becomes a page."""
    if name:
        text = re.sub(rf"^\s*(?:hey\s+)?{re.escape(name)}\b[\s,.!:]*", "", text, flags=re.I)
    m = _OPEN_ANY.match(_TRAILING_POLITE.sub("", text))
    return clean_note(m["name"]).rstrip("?!") if m else None


def is_cancel(text: str) -> bool:
    return bool(_CANCEL.match(text))


def _launch_command(viewer: str, path: Path, args: Sequence[str] = ()) -> str | list[str]:
    """How to start the viewer so it shows up and outlives the app.

    On Windows this goes through `cmd /c start`: the desktop shell starts the
    app with its window hidden, and a direct child could inherit that, and
    quitting the app ends its whole process tree, which would close the editor
    too. `start` opens the window normally and leaves it on its own.
    """
    if sys.platform == "win32":
        # A string, not a list: list2cmdline would escape start's "" title.
        # Windows paths can't contain quotes, so plain quoting is safe.
        if viewer.lower() == "default":
            return f'cmd /c start "" "{path}"'
        extra = "".join(f'"{a}" ' for a in args)
        return f'cmd /c start "" "{viewer}" {extra}"{path}"'
    if viewer.lower() == "default":
        return ["open" if sys.platform == "darwin" else "xdg-open", str(path)]
    return [viewer, *args, str(path)]


def viewer_args(viewer: str, configured: Sequence[str] | None) -> list[str]:
    """Extra arguments for the viewer. Unset means: Notepad++ gets -monitor, so
    each page opens with Monitoring (tail -f) on and updates while you're in
    another window; anything else gets none."""
    if configured is not None:
        return list(configured)
    name = re.split(r"[\\/]", viewer)[-1].lower()
    return ["-monitor"] if name == "notepad++.exe" else []


def _launch_options() -> dict:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}  # no flash of a console
    return {"start_new_session": True}


# Speech says "test test 2" for a page named "Test Test II", so numbers match
# whether written as digits, words or Roman numerals.
_NUMBER_WORDS = {
    **{
        w: str(i)
        for i, w in enumerate(
            ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]
        )
    },
    **{
        r: str(i)
        for i, r in enumerate(["_", "i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"])
        if i
    },
}


# Words speech-to-text writes for a spoken number: "Test For" for Test4.
# Only a fallback, so a real page "Notes for Sam" still matches as said.
_SOUND_ALIKES = {"for": "4", "fore": "4", "to": "2", "too": "2", "won": "1", "ate": "8"}


def _page_key(name: str, sound_alike: bool = False) -> str:
    words = re.findall(r"[a-z0-9]+", name.lower())
    if sound_alike:
        words = [_SOUND_ALIKES.get(w, w) for w in words]
    return "".join(_NUMBER_WORDS.get(w, w) for w in words)


def page_file_name(name: str) -> str:
    """A safe Windows file name for a page: "Escape room" -> "Escape room.md"."""
    name = _BAD_NAME_CHARS.sub(" ", name)
    name = " ".join(name.split()).strip(" .")[:80].rstrip(" .")
    return f"{name or 'Notes'}.md"


_NOTE_LINE = re.compile(r"^(?P<head>-\s+(?:\[\d{1,2}:\d{2}\]\s+)?)(?P<note>.*?)\s*$")


class Notebook:
    """The notes folder. A page you open or start is used for the rest of the
    session; if you take a note without choosing one, she starts her own page,
    named from `notes.default_page` (by default JuniperNotes_<date>_<n>)."""

    def __init__(self, config: NotesConfig, now=dt.datetime.now):
        self._config = config
        self._now = now
        self._in_use: Path | None = None  # the page chosen or started this session
        self.before: Path | None = None  # the page in use before that one

    @property
    def _current(self) -> Path | None:
        return self._in_use

    @_current.setter
    def _current(self, path: Path | None) -> None:
        if path != self._in_use and self._in_use is not None:
            self.before = self._in_use
        self._in_use = path

    def recent_pages(self, count: int = 8) -> list[Path]:
        """Pages, most recently written first."""
        if not self.dir.is_dir():
            return []
        pages = sorted(self.dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        return pages[:count]

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    @property
    def dir(self) -> Path:
        return self._config.dir

    def current_page(self) -> Path:
        """The page in use: the one chosen this session, else the newest page
        (for reading or opening), else the page she would start."""
        if self._current is not None:
            return self._current
        pages = list(self.dir.glob("*.md")) if self.dir.is_dir() else []
        if pages:
            return max(pages, key=lambda p: p.stat().st_mtime)
        return self._session_page()

    def recent_page(self, hours: float = 12) -> Path | None:
        """The page worth showing her for questions: the one in use this
        session, or one written in the last `hours`. None if there's neither."""
        if self._current is not None:
            return self._current if self._current.is_file() else None
        page = self.current_page()
        if not page.is_file():
            return None
        age = self._now().timestamp() - page.stat().st_mtime
        return page if age <= hours * 3600 else None

    def _session_page(self) -> Path:
        """Her own page for this session: the first unused index for today."""
        template = self._config.default_page
        today = f"{self._now():%Y-%m-%d}"
        for index in range(1, 1000):
            name = template.replace("{date}", today).replace("{index}", str(index))
            path = self.dir / page_file_name(name)
            if not path.exists() or "{index}" not in template:
                return path
        return path

    def latest_page(self) -> Path:
        """Switch to the page written most recently (the current one if none exist)."""
        pages = list(self.dir.glob("*.md")) if self.dir.is_dir() else []
        if pages:
            self._current = max(pages, key=lambda p: p.stat().st_mtime)
        return self.current_page()

    def find_page(self, name: str) -> Path | None:
        """An existing page whose name matches, ignoring case, spaces and
        punctuation, since speech gives "test monitor on" for testMonitorOn."""
        key = _page_key(name)
        if not key or not self.dir.is_dir():
            return None
        exact = self.dir / page_file_name(name)
        if exact.is_file():
            return exact
        pages = sorted(self.dir.glob("*.md"))
        if match := next((p for p in pages if _page_key(p.stem) == key), None):
            return match
        key = _page_key(name, sound_alike=True)
        return next((p for p in pages if _page_key(p.stem, sound_alike=True) == key), None)

    def previous_page(self) -> Path | None:
        """Step back one page, by when each was last written, from the current
        one, and make it current. None when there's nothing older."""
        pages = sorted(
            self.dir.glob("*.md") if self.dir.is_dir() else [],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        current = self.current_page()
        index = pages.index(current) + 1 if current in pages else 0
        if index >= len(pages) or pages[index] == current:
            return None
        self._current = pages[index]
        return self._current

    def page_exists(self, name: str) -> bool:
        return self.find_page(name) is not None

    def new_page(self, name: str) -> Path:
        """Start a page (or switch back to one that already exists) and make it current."""
        if not name:
            name = f"Notes {self._now():%Y-%m-%d %H%M}"
        path = self.find_page(name) or self.dir / page_file_name(name)
        if not path.exists():
            self._create(path)
        self._current = path
        return path

    def rename(self, name: str) -> Path:
        """Rename the current page (file and heading) and keep using it.
        FileExistsError if another page already has that name."""
        old = self.current_page()
        new = self.dir / page_file_name(name)
        if not old.exists():
            self._create(new)
        elif new != old:
            other = self.find_page(name)
            if (other and other != old) or (new.exists() and new.name.lower() != old.name.lower()):
                raise FileExistsError(f"a page called {new.stem} already exists")
            text = old.read_text(encoding="utf-8")
            if text.startswith(f"# {old.stem}\n"):
                text = f"# {new.stem}\n" + text[len(old.stem) + 3 :]
            old.rename(new)
            new.write_text(text, encoding="utf-8", newline="\n")
            if self.viewer:  # Notepad++ will ask about the old tab: answer it
                dismiss_in_background(self.dir)
        before = self.before
        self._current = new
        self.before = before if before != new else None
        log.info("Renamed %s to %s", old.name, new.name)
        return new

    def write(self, note: str) -> Path:
        """Append one note to the current page and flush it to disk."""
        if self._current is None:  # nothing opened this session: start her own page
            self._current = self._session_page()
        path = self._current
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

    def last_notes(self, count: int = 5, page: Path | None = None) -> list[str]:
        """The newest notes on a page (the current one by default), oldest
        first, without the "- [12:34] "."""
        path = page or self.current_page()
        if not path.is_file():
            return []
        notes = [
            _NOTE_LINE.match(line)["note"]
            for line in path.read_text(encoding="utf-8").splitlines()
            if _NOTE_LINE.match(line) and not _NOTE_LINE.match(line)["note"].startswith("~~")
        ]
        return notes[-count:]

    def search(self, what: str, skip: Path | None = None, pages: int = 10) -> list[tuple[str, str]]:
        """Notes about `what` on the newest `pages` pages other than `skip`, as
        (page, note), newest page first. Crossed-off notes are left out."""
        topic = topic_words(what) - _KIND_WORDS
        if not topic or not self.dir.is_dir():
            return []
        found = []
        newest = sorted(self.dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        for page in [p for p in newest if p != skip][:pages]:
            found += [(page.stem, n) for n in self.last_notes(1000, page) if topic & set(_words(n))]
        return found

    def mark_used(self, what: str, anywhere: bool = False) -> int:
        """Strike through notes about `what` on the current page ("the vault
        code" strikes every note mentioning the vault; "gate 3258" only the one
        with 3258). With `anywhere`, if the current page has none, the 10 newest
        other pages are tried. Nothing is deleted. Returns how many were struck."""
        topic = topic_words(what)
        if not topic:
            return 0
        digits = "".join(re.findall(r"\d", what))
        current = self.current_page()
        pages = [current]
        if anywhere and self.dir.is_dir():
            newest = sorted(self.dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
            pages += [p for p in newest if p != current][:10]
        for path in pages:
            if struck := self._strike(path, topic - ({w for w in topic if w.isdigit()}), digits):
                return struck
        return 0

    @staticmethod
    def _strike(path: Path, topic: set[str], digits: str) -> int:
        if not path.is_file():
            return 0
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        struck = 0
        for i, line in enumerate(lines):
            m = _NOTE_LINE.match(line.rstrip("\n"))
            if not m or m["note"].startswith("~~"):
                continue
            note = m["note"]
            if digits:  # a number names one note exactly
                hit = digits in "".join(re.findall(r"\d", note))
                hit = hit and (not topic or bool(topic & set(_words(note))))
            else:
                hit = bool(topic & set(_words(note)))
            if hit:
                lines[i] = f"{m['head']}~~{note}~~\n"
                struck += 1
        if struck:
            with path.open("w", encoding="utf-8") as f:
                f.write("".join(lines))
                f.flush()
                os.fsync(f.fileno())
        return struck

    @property
    def viewer(self) -> str:
        return self._config.open_with.strip()

    def open(self, path: Path | None = None) -> bool:
        """Show a page in the viewer from `notes.open_with`. False if none is set
        or it couldn't be started."""
        if path is None:  # "open my notes": that page is now the one in use
            path = self._current = self.current_page()
        viewer = self.viewer
        if not viewer:
            return False
        if not path.exists():
            self._create(path)
        is_path = "/" in viewer or "\\" in viewer
        if is_path and not Path(viewer).is_file():
            log.error("notes.open_with %s doesn't exist", viewer)
            return False
        try:
            args = viewer_args(viewer, self._config.open_args)
            subprocess.Popen(_launch_command(viewer, path, args), **_launch_options())
        except OSError:
            log.exception("Couldn't open %s with %s", path, viewer)
            return False
        log.info("Opened %s with %s", path.name, viewer)
        # Notepad++ rechecks its tabs when it comes forward and may ask about a
        # page renamed earlier: answer that too.
        dismiss_in_background(self.dir, timeout=10.0)
        return True

    def _create(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as f:
            f.write(f"# {path.stem}\n\n")
