"""Note actions the model takes itself, written as tags in its reply.

She can write "[note: vault code 5526]", "[cross off: vault]",
"[open page: Escape room]" or "[rename page: Haunted House]" anywhere in a
reply. `ActionFilter` pulls these out of the token stream before anything is
shown or spoken (like mood tags), and the app carries them out once the reply
is done. Fixed phrases such as
"write that down: ..." still work without the model; these tags are for
everything said in other words.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

_KINDS = {
    "note": "write",
    "write": "write",
    "cross off": "cross_off",
    "crossoff": "cross_off",
    "open page": "open_page",
    "page": "open_page",
    "rename page": "rename_page",
    "rename": "rename_page",
}
_ACTION = re.compile(
    r"\[\s*(note|write|cross\s*off|open\s*page|page|rename(?:\s*page)?)\s*:\s*([^\]\n]{1,200}?)\s*\]\s*",
    re.IGNORECASE,
)
_PREFIX = re.compile(r"\[\s*[a-z ]{0,14}(?::[^\]\n]*)?$", re.IGNORECASE)
_MAX = 230  # longer than any action tag; past this a "[" is just text


@dataclass(frozen=True)
class Action:
    kind: Literal["write", "cross_off", "open_page", "rename_page"]
    text: str


def _action(m: re.Match[str]) -> Action:
    kind = _KINDS[re.sub(r"\s+", " ", m.group(1).lower())]
    return Action(kind, m.group(2).strip().strip("\"'“”"))  # type: ignore[arg-type]


class ActionFilter:
    def __init__(self) -> None:
        self._buf = ""
        self.actions: list[Action] = []

    def feed(self, text: str) -> str:
        """Text with action tags removed; holds back a possible partial tag."""
        self._buf += text
        out: list[str] = []
        while self._buf:
            i = self._buf.find("[")
            if i < 0:
                out.append(self._buf)
                self._buf = ""
                break
            out.append(self._buf[:i])
            self._buf = self._buf[i:]
            if m := _ACTION.match(self._buf):
                self.actions.append(_action(m))
                self._buf = self._buf[m.end() :]
                continue
            if _PREFIX.match(self._buf) and len(self._buf) < _MAX:
                break  # might be the start of an action: wait for more tokens
            out.append("[")
            self._buf = self._buf[1:]
        return "".join(out)

    def flush(self) -> str:
        out, self._buf = self._buf, ""
        return out


def strip_actions(text: str) -> str:
    return _ACTION.sub("", text).strip()


def as_tags(actions: list[Action]) -> str:
    """The actions written back as tags, so her history shows she used them."""
    names = {
        "write": "note",
        "cross_off": "cross off",
        "open_page": "open page",
        "rename_page": "rename page",
    }
    return " ".join(f"[{names[a.kind]}: {a.text}]" for a in actions)
