"""Mood tags: the model starts each reply with one, the avatar shows it.

The model writes a tag such as "[happy]" before its reply. `TagFilter` pulls
tags out of the token stream before anything is shown or spoken, and reports
which mood was asked for. The set of moods is fixed here, in code; the persona
file only decides how each mood looks on the avatar (see `Expression`).

Small models don't always follow the format, so the filter is forgiving:
- no tag at all            -> mood stays None (the app shows neutral)
- an unknown leading tag   -> stripped, reported as "neutral"
- a known tag mid-reply    -> stripped, reported as a mood change
- any other "[...]" mid-reply is left alone (it's probably real text)
"""

from __future__ import annotations

import re

EMOTIONS = ("neutral", "happy", "surprised", "sad", "relaxed")

_TAG = re.compile(r"\[\s*([A-Za-z]+)\s*\]")
_MAX_TAG = 16  # longer than any tag; past this a "[" is just text
_ANY_TAG = re.compile(r"\[\s*(" + "|".join(EMOTIONS) + r")\s*\]\s*", re.IGNORECASE)


class TagFilter:
    def __init__(self) -> None:
        self._buf = ""
        self._at_start = True  # nothing but whitespace released so far
        self._eat_space = False
        self._ends_with_space = False
        self.moods: list[str] = []  # every mood seen, in order

    @property
    def mood(self) -> str | None:
        """The mood the reply opened with, or None if it had no tag."""
        return self.moods[0] if self.moods else None

    def feed(self, text: str) -> tuple[str, list[str]]:
        """Return (text safe to show and speak, moods found in this chunk)."""
        self._buf += text
        out: list[str] = []
        found: list[str] = []
        while self._buf:
            i = self._buf.find("[")
            if i < 0:
                self._release(out, self._buf)
                self._buf = ""
                break
            if i > 0:
                self._release(out, self._buf[:i])
                self._buf = self._buf[i:]
            m = _TAG.match(self._buf)
            if m:
                mood = self._mood(m.group(1))
                if mood is None:  # an ordinary bracket in the text
                    self._release(out, m.group(0))
                else:
                    found.append(mood)
                    # Drop the space after the tag, so neither "[happy] Hi" nor
                    # "Done. [happy] Hi" leaves a stray or doubled space.
                    self._eat_space = self._at_start or self._ends_with_space
                self._buf = self._buf[m.end() :]
                continue
            if "]" in self._buf or len(self._buf) >= _MAX_TAG:
                # Not a tag after all: release the "[" and keep scanning after it.
                self._release(out, "[")
                self._buf = self._buf[1:]
                continue
            break  # might be the start of a tag: wait for more tokens
        self.moods += found
        return "".join(out), found

    def flush(self) -> str:
        out: list[str] = []
        self._release(out, self._buf)
        self._buf = ""
        return "".join(out)

    def _mood(self, word: str) -> str | None:
        word = word.lower()
        if word in EMOTIONS:
            return word
        # An unknown tag at the very start is still clearly meant as a mood tag.
        return "neutral" if self._at_start and not self.moods else None

    def _release(self, out: list[str], text: str) -> None:
        if not text:
            return
        if self._at_start or self._eat_space:
            text = text.lstrip()
            if not text:
                return
            self._eat_space = False
        self._at_start = False
        self._ends_with_space = text[-1].isspace()
        out.append(text)


def strip_tags(text: str) -> str:
    """Remove mood tags from a finished reply (e.g. history sent to Claude)."""
    return _ANY_TAG.sub("", text).strip()
