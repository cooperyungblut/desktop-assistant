"""Pick out the names in a brief so speech recognition spells them right.

Whisper accepts an `initial_prompt`: text it treats as what was said just
before the clip. Listing the brief's names there ("Northwind Traders, Dana
Reyes, ...") strongly biases it toward those spellings, which fixes mishearings
like "Northwind Traitors" without a bigger model.

The extraction is a heuristic, not NLP: runs of Capitalized Words, plus single
capitalized words that aren't just starting a sentence or line.
"""

from __future__ import annotations

import re
import string

from .briefs import Brief
from .persona import Persona

MAX_PROMPT_CHARS = 400  # Whisper only uses the last ~224 tokens of the prompt

_CAP_RUN = re.compile(r"\b[A-Z][A-Za-z0-9&'.-]*(?:\s+[A-Z][A-Za-z0-9&'.-]*)*")
_MARKUP = " \t*_`>#|-(" + string.digits  # skipped when looking back from a word
_STOPWORDS = {
    "i", "am", "pm", "ct", "et", "pt", "fyi", "ok", "tbd", "re", "fw", "fwd",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "mon", "tue", "wed", "thu", "fri", "sat", "sun",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    "sample", "time", "event",
}  # fmt: skip


def brief_terms(brief: Brief) -> list[str]:
    """Proper nouns from the brief's display text, in order of first appearance."""
    terms: dict[str, None] = {}
    for line in brief.display.splitlines():
        for m in _CAP_RUN.finditer(line):
            words = [w.rstrip(".'") for w in m.group().split()]
            while words and words[0].lower() in _STOPWORDS:
                words.pop(0)
            while words and words[-1].lower() in _STOPWORDS:
                words.pop()
            if not words:
                continue
            at_start = _starts_sentence(line[: m.start()])
            if len(words) == 1 and (at_start or len(words[0]) < 3):
                continue  # "Asks", "The", "Calendar" heading...
            terms.setdefault(" ".join(words), None)
    return list(terms)


def speech_hints(brief: Brief | None, persona: Persona) -> str:
    """The `initial_prompt` for Whisper: assistant, user and brief names."""
    names = [persona.name, persona.user_name, "Claude"]
    if brief is not None:
        names += brief_terms(brief)
    seen: dict[str, None] = {}
    for name in names:
        seen.setdefault(name, None)
    prompt = "Names: " + ", ".join(seen) + "."
    if len(prompt) > MAX_PROMPT_CHARS:
        prompt = prompt[:MAX_PROMPT_CHARS].rsplit(",", 1)[0] + "."
    return prompt


def _starts_sentence(before: str) -> bool:
    stripped = before.rstrip(_MARKUP)
    return not stripped or stripped[-1] in ".!?:;|"
