"""Retrieval eval for the Wikipedia lookup: does search hand her the answer?

Each case is a question, the article(s) that answer it and a pattern for the
answer itself. A case runs through the same steps as the app (lookup_request ->
search -> usable -> render), so it measures exactly what she would read:

    article  an expected article is among the passages she gets (recall@4)
    answer   the answer pattern appears in the text she gets, after clipping
    rank     where the first expected article sits (1 = first passage)

Used by scripts/eval_wiki.py. The model is not involved: a miss here is a
search miss, a hit here that she still gets wrong is a prompt/model problem.
"""

from __future__ import annotations

import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .lookup import Lookup, lookup_request, render, usable
from .wiki import Passage

SEARCH_LIMIT = 8  # what the app asks search for


@dataclass(frozen=True)
class RetrievalCase:
    question: str
    articles: tuple[str, ...]  # any of these counts as the right article
    answer: str  # regex, case-sensitive; matched against what she reads
    note: str = ""


@dataclass
class RetrievalResult:
    case: RetrievalCase
    looked_up: bool  # False when the app wouldn't look this question up at all
    titles: list[str] = field(default_factory=list)
    text: str = ""
    ms: float = 0.0

    @property
    def rank(self) -> int | None:
        wanted = {title.lower() for title in self.case.articles}
        for at, title in enumerate(self.titles, 1):
            if title.lower() in wanted:
                return at
        return None

    @property
    def article_ok(self) -> bool:
        return self.rank is not None

    @property
    def answer_ok(self) -> bool:
        return bool(re.search(self.case.answer, self.text))


def load_retrieval_cases(path: Path) -> list[RetrievalCase]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    cases = []
    for item in raw:
        articles = item["article"]
        cases.append(
            RetrievalCase(
                question=item["question"],
                articles=tuple([articles] if isinstance(articles, str) else articles),
                answer=item["answer"],
                note=item.get("note", ""),
            )
        )
    return cases


def run_retrieval_case(wiki: Lookup, case: RetrievalCase) -> RetrievalResult:
    request = lookup_request(case.question)
    if request is None:
        return RetrievalResult(case=case, looked_up=False)
    started = time.perf_counter()
    found = wiki.search(request.query, SEARCH_LIMIT, loose=request.forced)
    ms = (time.perf_counter() - started) * 1000
    passages: Sequence[Passage] = usable(found, request)
    return RetrievalResult(
        case=case,
        looked_up=True,
        titles=[p.title for p in passages],
        text=render(passages),
        ms=ms,
    )


def summary(results: Sequence[RetrievalResult]) -> str:
    n = len(results) or 1
    articles = sum(r.article_ok for r in results)
    answers = sum(r.answer_ok for r in results)
    ranks = [r.rank for r in results if r.rank]
    mrr = sum(1 / rank for rank in ranks) / n
    times = sorted(r.ms for r in results if r.looked_up)
    median = times[len(times) // 2] if times else 0.0
    return (
        f"article {articles}/{len(results)} ({articles / n:.0%})  "
        f"answer {answers}/{len(results)} ({answers / n:.0%})  "
        f"MRR {mrr:.2f}  median {median:.0f} ms"
    )
