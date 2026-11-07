"""Fact-check evaluation of local chat models.

Asks each model the questions in evals/cases.yaml, the same way the app does,
and checks whether the facts survived. Used by scripts/eval_models.py; kept
here so the scoring logic is tested like the rest of the app.

For each answer it checks:
- facts:   every `must` group matched, and nothing in `must_not` appeared
- unknown: for questions the brief can't answer, she said she didn't know
- tag:     the reply opened with a valid mood tag (and an expected one, if set)
- speed:   seconds to the first visible token, and to the whole reply
"""

from __future__ import annotations

import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from .briefs import Brief
from .expressions import EMOTIONS, TagFilter
from .llm import ChatModel
from .persona import Persona
from .prompt import Turn, build_messages

# Ways a reply can say "that isn't in the brief". Matched after normalizing.
DONT_KNOW = (
    "don't know",
    "do not know",
    "don't see",
    "do not see",
    "doesn't say",
    "does not say",
    "doesn't mention",
    "does not mention",
    "not in the brief",
    "isn't in the brief",
    "no information",
    "not sure",
    "doesn't include",
    "does not include",
    "doesn't list",
    "not listed",
    "not mentioned",
    "no details",
)

# She should never send Cooper off to Claude on her own: /claude is a command he
# types when he wants it, not her way out of a question. Always forbidden.
SUGGESTS_CLAUDE = ("/claude", "check with claude", "ask claude")


class HistoryTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class Case(BaseModel):
    id: str
    brief: str
    question: str
    # Turns after the brief readout and before the question, e.g. a hedged
    # answer for an "are you sure?" follow-up.
    history: list[HistoryTurn] = []
    must: list[list[str]] = []
    must_not: list[str] = []
    unknown: bool = False
    moods: list[str] = Field(default_factory=list)


def load_cases(path: Path) -> list[Case]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = [Case.model_validate(c) for c in raw["cases"]]
    ids = [c.id for c in cases]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate case ids: {', '.join(sorted(dupes))}")
    for c in cases:
        bad = set(c.moods) - set(EMOTIONS)
        if bad:
            raise ValueError(f"{c.id}: unknown moods {sorted(bad)}")
    return cases


def normalize(text: str) -> str:
    """Lowercase, straight quotes, single spaces; keep ':', '$' and '/' for times and money."""
    text = text.lower().replace("’", "'").replace("‘", "'")
    text = re.sub(r"[^a-z0-9':$/\- ]+", " ", text)
    return " " + re.sub(r"\s+", " ", text).strip() + " "


def _contains(answer: str, phrase: str) -> bool:
    """`answer` must already be normalized. Short words and numbers match whole
    words only ("one" not in "someone", "3" not in "30"); longer phrases and
    symbols like "$" match anywhere."""
    p = normalize(phrase).strip()
    if not p:
        return False
    if p.isalnum() and len(p) <= 3:
        return f" {p} " in answer
    return p in answer


@dataclass
class Result:
    case: str
    answer: str
    mood: str | None
    first_token_s: float
    total_s: float
    missing: list[list[str]] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    said_unknown: bool | None = None  # only set for `unknown` cases
    mood_ok: bool = True
    error: str | None = None

    @property
    def facts_ok(self) -> bool:
        if self.error:
            return False
        return not self.missing and not self.forbidden and self.said_unknown is not False

    @property
    def tag_ok(self) -> bool:
        return self.mood is not None


def score(case: Case, answer: str, mood: str | None) -> dict[str, Any]:
    text = normalize(answer)
    missing = [g for g in case.must if not any(_contains(text, alt) for alt in g)]
    forbidden = [p for p in (*case.must_not, *SUGGESTS_CLAUDE) if _contains(text, p)]
    said_unknown = any(_contains(text, p) for p in DONT_KNOW) if case.unknown else None
    mood_ok = mood is not None and (not case.moods or mood in case.moods)
    return {
        "missing": missing,
        "forbidden": forbidden,
        "said_unknown": said_unknown,
        "mood_ok": mood_ok,
    }


async def ask(
    model: ChatModel,
    persona: Persona,
    brief: Brief,
    question: str,
    earlier: Sequence[HistoryTurn] = (),
) -> tuple[str, str | None, float, float]:
    """One question, built exactly like a follow-up in the app.

    Returns (visible answer, mood tag or None, seconds to first visible text,
    seconds in total).
    """
    history = [Turn("assistant", brief.spoken)]  # she just read the brief aloud
    history += [Turn(t.role, t.content) for t in earlier]
    messages = build_messages(persona, brief, history, question)
    tags = TagFilter()
    parts: list[str] = []
    start = time.perf_counter()
    first = None
    async for token in model.stream_chat(messages, persona.sampling.as_options()):
        text, _ = tags.feed(token)
        if text:
            first = first if first is not None else time.perf_counter() - start
            parts.append(text)
    parts.append(tags.flush())
    total = time.perf_counter() - start
    return "".join(parts).strip(), tags.mood, first if first is not None else total, total


async def run_case(model: ChatModel, persona: Persona, brief: Brief, case: Case) -> Result:
    try:
        answer, mood, first, total = await ask(model, persona, brief, case.question, case.history)
    except Exception as exc:  # report it in the table, keep going
        return Result(case.id, "", None, 0.0, 0.0, error=str(exc), mood_ok=False)
    return Result(case.id, answer, mood, first, total, **score(case, answer, mood))


@dataclass
class Summary:
    model: str
    results: list[Result]

    def _share(self, pick) -> float:
        rs = [r for r in self.results if pick(r) is not None]
        return sum(1 for r in rs if pick(r)) / len(rs) if rs else 1.0

    @property
    def facts(self) -> float:
        return self._share(lambda r: r.facts_ok)

    @property
    def unknowns(self) -> float:
        return self._share(lambda r: r.said_unknown)

    @property
    def tags(self) -> float:
        return self._share(lambda r: r.tag_ok)

    @property
    def moods(self) -> float:
        return self._share(lambda r: r.mood_ok)

    @property
    def errors(self) -> int:
        return sum(1 for r in self.results if r.error)

    def median(self, attr: str) -> float:
        xs = sorted(getattr(r, attr) for r in self.results if r.error is None)
        if not xs:
            return 0.0
        mid = len(xs) // 2
        return xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2


def table(summaries: Sequence[Summary]) -> str:
    head = (
        f"{'model':<22} {'facts':>6} {'unknown':>8} {'tag':>5} {'mood':>5} "
        f"{'first':>6} {'total':>6} {'errors':>6}"
    )
    lines = [head, "-" * len(head)]
    for s in summaries:
        lines.append(
            f"{s.model:<22} {s.facts:>6.0%} {s.unknowns:>8.0%} {s.tags:>5.0%} "
            f"{s.moods:>5.0%} {s.median('first_token_s'):>5.1f}s "
            f"{s.median('total_s'):>5.1f}s {s.errors:>6}"
        )
    return "\n".join(lines)


def failures(summary: Summary) -> list[str]:
    """Readable lines for every check that failed, to see *why* a model lost points."""
    out = []
    for r in summary.results:
        problems = []
        if r.error:
            problems.append(f"error: {r.error}")
        if r.missing:
            problems.append("missing " + "; ".join(" / ".join(g) for g in r.missing))
        if r.forbidden:
            problems.append("said " + ", ".join(repr(p) for p in r.forbidden))
        if r.said_unknown is False:
            problems.append("guessed instead of saying it doesn't know")
        if not r.tag_ok and not r.error:
            problems.append("no mood tag")
        elif not r.mood_ok and not r.error:
            problems.append(f"mood [{r.mood}] doesn't fit")
        if problems:
            out.append(f"  {r.case}: {'; '.join(problems)}\n    -> {r.answer!r}")
    return out
