"""Spoken or typed requests the app handles itself instead of the chat model.

Right now just one: "read me this morning's brief". Like escalation, this is a
deliberately narrow pattern match in code. The readout is read verbatim, so
it must never depend on the local model deciding to do it.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable
from dataclasses import dataclass

from .briefs import Brief, BriefType

# A verb that asks for a readout, then (within a few words) the word "brief".
# "What did the brief say?" and "Read the email in the brief" don't match.
_READ = re.compile(
    r"\b(read|reread|re-read|replay|play|go\s+over|run\s+through)\b"
    r"(?P<middle>(?:\s+[\w'’]+){0,5}?)\s+brief(?:ing)?\b",
    re.IGNORECASE,
)
_QUESTION_START = re.compile(r"^\s*(what|when|who|where|why|how|which|did|does|is|was)\b", re.I)
_TYPES = {t.value: t for t in BriefType}


@dataclass(frozen=True)
class BriefRequest:
    type: BriefType | None = None  # None: any type
    day: str | None = None  # "today", "yesterday" or None (newest)
    sample: bool = False


def brief_request(text: str) -> BriefRequest | None:
    """`BriefRequest` if the text asks to hear a brief, else None."""
    if len(text.split()) > 16 or _QUESTION_START.match(text):
        return None
    m = _READ.search(text)
    if not m:
        return None
    words = re.findall(r"[a-z]+", text.lower())
    kind = next((_TYPES[w] for w in words if w in _TYPES), None)
    if kind is None and "tonight" in words:
        kind = BriefType.EVENING
    lowered = text.lower()
    if "yesterday" in words:
        day = "yesterday"
    elif {"today", "tonight"} & set(words) or re.search(
        r"\bthis\s+(morning|afternoon|evening)", lowered
    ):
        day = "today"
    else:
        day = None
    return BriefRequest(type=kind, day=day, sample="sample" in words)


def find_brief(req: BriefRequest, briefs: Iterable[Brief], today: dt.date) -> Brief | None:
    """The newest brief matching the request. Real briefs win over samples
    unless the request says "sample"."""
    pool = [b for b in briefs if req.type is None or b.type == req.type]
    if req.day == "today":
        pool = [b for b in pool if b.date == today]
    elif req.day == "yesterday":
        pool = [b for b in pool if b.date == today - dt.timedelta(days=1)]
    real = [b for b in pool if not b.sample]
    fake = [b for b in pool if b.sample]
    pool = fake if req.sample else (real or fake)
    return max(pool, key=lambda b: b.generated_at, default=None)


def describe(req: BriefRequest) -> str:
    """'a morning brief from today', for "I don't have ..." replies."""
    if req.sample:
        what = f"a sample {req.type.value + ' ' if req.type else ''}brief"
    elif req.type:
        article = "an" if req.type.value[0] in "aeiou" else "a"
        what = f"{article} {req.type.value} brief"
    else:
        what = "any briefs"
    when = {"today": " from today", "yesterday": " from yesterday"}.get(req.day or "", "")
    return what + when
