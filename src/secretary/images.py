"""Pictures she can look at, sent to the model's vision with the question.

Today a picture comes from the chat panel (pasted or dropped in). Screen-look
will add a second source (a screenshot taken on request), so a picture carries
where it came from: the prompt tells her which, and nothing else needs to change.

Pictures are kept as base64, because that is what Ollama's /api/chat takes
(`"images": [...]` on a message) and what the browser sends over the WebSocket.
They live in memory only, for the conversation; nothing is written to disk.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

Source = Literal["pasted", "screen"]

MAX_PICTURES = 4  # per question
# The chat panel shrinks pictures to about 1,600 px before sending (a few hundred KB),
# so this only stops something unreasonable, like a raw 50 MB photo.
MAX_PICTURE_BYTES = 8_000_000

# Formats Ollama's image decoder reads, by their first bytes.
_SIGNATURES: dict[bytes, str] = {
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
}


@dataclass(frozen=True)
class Picture:
    b64: str  # base64, no "data:" prefix
    mime: str
    source: Source = "pasted"


def picture_mime(data: bytes) -> str | None:
    """The image type from the file's first bytes, or None if it isn't PNG/JPEG."""
    for sig, mime in _SIGNATURES.items():
        if data.startswith(sig):
            return mime
    return None


def decode_picture(value: Any, source: Source = "pasted") -> Picture | None:
    """One picture from the browser: base64 or a data: URL. None if it isn't one."""
    if not isinstance(value, str):
        return None
    if value.startswith("data:"):
        _, _, value = value.partition(",")
    if not value or len(value) > MAX_PICTURE_BYTES * 4 // 3 + 4:
        return None
    try:
        data = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None
    mime = picture_mime(data)
    if mime is None:
        return None
    return Picture(b64=value, mime=mime, source=source)


def decode_pictures(values: Any, source: Source = "pasted") -> list[Picture]:
    """The usable pictures from a list sent by the browser (bad ones are skipped)."""
    if not isinstance(values, list):
        return []
    return [p for v in values[:MAX_PICTURES] if (p := decode_picture(v, source))]


def describe(pictures: Sequence[Picture]) -> str:
    """A short phrase for the prompt: "a picture" or "2 pictures", plus the source."""
    n = len(pictures)
    what = "a picture" if n == 1 else f"{n} pictures"
    if pictures and all(p.source == "screen" for p in pictures):
        return f"{what} of their screen"
    return what
