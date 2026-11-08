from __future__ import annotations

from pathlib import Path

from secretary.retrieval_eval import (
    RetrievalCase,
    load_retrieval_cases,
    run_retrieval_case,
    summary,
)
from secretary.wiki import Passage

ROOT = Path(__file__).resolve().parents[1]


class FakeWiki:
    def __init__(self, passages: list[Passage]) -> None:
        self.passages = passages
        self.asked: list[tuple[str, int, bool]] = []

    def search(self, question: str, limit: int = 3, *, loose: bool = True) -> list[Passage]:
        self.asked.append((question, limit, loose))
        return self.passages

    def interrupt(self) -> None:
        pass


ISLE = Passage(
    "Isle of Man",
    "Facts: Motto: Quocunque Jeceris Stabit (Latin); Capital: Douglas.",
    1.0,
    "title",
)
OTHER = Passage("Mottos of Romanian institutions", "Romania has no official motto.", 0.5, "all")
CASE = RetrievalCase(
    "What is the official motto of the Isle of Man?", ("Isle of Man",), "Quocunque"
)


def test_a_hit_finds_article_rank_and_answer():
    wiki = FakeWiki([OTHER, ISLE])
    result = run_retrieval_case(wiki, CASE)
    assert result.looked_up and result.article_ok and result.answer_ok
    assert result.rank == 2
    # The same search the app runs for a plain question.
    assert wiki.asked == [(CASE.question, 8, False)]


def test_a_miss_and_the_summary():
    result = run_retrieval_case(FakeWiki([OTHER]), CASE)
    assert not result.article_ok and not result.answer_ok and result.rank is None
    hit = run_retrieval_case(FakeWiki([ISLE]), CASE)
    line = summary([result, hit])
    assert line.startswith("article 1/2 (50%)  answer 1/2 (50%)  MRR 0.50")


def test_questions_the_app_would_not_look_up_are_skipped():
    case = RetrievalCase("What is 12 times 7?", ("Multiplication",), "84")
    result = run_retrieval_case(FakeWiki([ISLE]), case)
    assert not result.looked_up and not result.article_ok


def test_the_shipped_cases_load_and_are_all_looked_up():
    cases = load_retrieval_cases(ROOT / "evals" / "wiki_questions.yaml")
    assert len(cases) >= 30
    for case in cases:
        assert case.articles and case.answer
        assert run_retrieval_case(FakeWiki([]), case).looked_up, case.question
