from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from secretary.wiki import (
    WikiIndex,
    build_index,
    is_indexable,
    iter_articles,
    query_terms,
    split_passages,
)

CAGE = (
    "Nicolas Kim Coppola (born January 7, 1964), known professionally as Nicolas Cage, "
    "is an American actor and film producer. He is known for a wide variety of roles "
    "across genres, and he has appeared in dozens of films since the early 1980s."
)
ARTICLES = [
    ("Nicolas Cage", CAGE + "\n\nEarly life\nHe was born in Long Beach, California."),
    (
        "Paris",
        "Paris is the capital and most populous city of France. " * 6
        + "\n\nThe city sits on the river Seine in the north of the country.",
    ),
    ("Mercury (planet)", "Mercury is the smallest planet in the Solar System. " * 6),
    ("Mercury", "Mercury may refer to:\n- the planet\n- the element " + "x" * 200),
    ("Tiny", "Too short."),
]


@pytest.fixture
def index_file(tmp_path: Path) -> Path:
    path = tmp_path / "wiki.sqlite"
    build_index(iter(ARTICLES), path)
    return path


def test_split_passages_keeps_opening_first_and_respects_limit():
    text = "First paragraph here.\n\n" + ("Sentence number one. " * 80)
    passages = split_passages(text, limit=300)
    assert passages[0].startswith("First paragraph here.")
    assert all(len(p) <= 300 for p in passages)
    assert len(passages) > 2


def test_headings_are_dropped_but_short_sentences_stay():
    text = (
        "Paris is the capital of France.\n\nEarly life\n\nHe was born in Lyon.\n"
        "Plot\nThe story of a deaf and mute hitman starts in the first act of the film."
    )
    assert split_passages(text) == [
        "Paris is the capital of France.\nHe was born in Lyon.\n"
        "The story of a deaf and mute hitman starts in the first act of the film."
    ]


def test_is_indexable_skips_stubs_and_disambiguation():
    assert not is_indexable("Tiny", "Too short.")
    assert not is_indexable("Mercury", ARTICLES[3][1])
    assert is_indexable("Paris", ARTICLES[1][1])


def test_query_terms_drops_question_words():
    assert query_terms("What is Nicolas Cage's birthday?") == ["nicolas", "cage", "birthday"]


def test_build_counts_and_skips(tmp_path: Path):
    stats = build_index(iter(ARTICLES), tmp_path / "w.sqlite")
    assert stats.articles == 3
    assert stats.skipped == 2
    assert not list(tmp_path.glob("*.building"))


def test_failed_build_leaves_no_index(tmp_path: Path):
    def broken():
        yield ARTICLES[0]
        raise RuntimeError("disk gone")

    target = tmp_path / "w.sqlite"
    with pytest.raises(RuntimeError):
        build_index(broken(), target)
    assert not target.exists()
    assert not list(tmp_path.glob("*.building"))


def test_birthday_question_finds_the_opening(index_file: Path):
    with WikiIndex(index_file) as index:
        top = index.search("What is Nicolas Cage's birthday?")[0]
    assert top.title == "Nicolas Cage"
    assert "born January 7, 1964" in top.text
    assert top.url == "https://en.wikipedia.org/wiki/Nicolas_Cage"


def test_misspelled_first_name_still_finds_the_article(index_file: Path):
    with WikiIndex(index_file) as index:
        titles = [p.title for p in index.search("What is Nicholas Cage's birthday?")]
    assert titles[0] == "Nicolas Cage"


def test_body_text_match_without_title(index_file: Path):
    with WikiIndex(index_file) as index:
        titles = [p.title for p in index.search("which river is the capital of France on")]
    assert "Paris" in titles


def test_title_match_beats_body_mentions(index_file: Path):
    with WikiIndex(index_file) as index:
        assert index.search("tell me about Mercury planet")[0].title == "Mercury (planet)"


def test_no_useful_words_or_no_matches_returns_nothing(index_file: Path):
    with WikiIndex(index_file) as index:
        assert index.search("what is it?") == []
        assert index.search("zzzqqq xylophonics") == []


def test_limit_and_no_duplicates(index_file: Path):
    with WikiIndex(index_file) as index:
        found = index.search("Paris France city", limit=2)
    assert len(found) <= 2
    assert len({(p.title, p.text) for p in found}) == len(found)


def test_missing_index_has_a_helpful_error(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="docs/wikipedia.md"):
        WikiIndex(tmp_path / "nope.sqlite")


def test_reads_parquet_files_in_order(tmp_path: Path):
    for n, rows in enumerate((ARTICLES[:2], ARTICLES[2:])):
        table = pa.table(
            {
                "id": [str(i) for i in range(len(rows))],
                "title": [t for t, _ in rows],
                "text": [x for _, x in rows],
            }
        )
        pq.write_table(table, tmp_path / f"train-{n:05d}-of-00002.parquet")
    assert [t for t, _ in iter_articles(tmp_path)] == [t for t, _ in ARTICLES]


def test_empty_source_dir_is_an_error(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        next(iter_articles(tmp_path))


def test_common_words_are_left_out_when_a_rarer_one_exists(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("secretary.wiki.COMMON_FLOOR", 0)
    filler = "The history of this place is long and the history matters. "
    articles = [(f"Town {n}", filler * 5) for n in range(200)]
    articles.append(("Zanzibar", "Zanzibar is an archipelago off the coast of Tanzania. " * 5))
    path = tmp_path / "w.sqlite"
    build_index(iter(articles), path)
    with WikiIndex(path) as index:
        assert index._prune_common(["history", "zanzibar"]) == ["zanzibar"]
        assert index._prune_common(["history"]) == ["history"]
        assert index._prune_common(["history", "qwxyz"]) == ["history"]
        # Stemming: "histories" is counted as the common stem "histori".
        assert index._prune_common(["histories", "zanzibar"]) == ["zanzibar"]
        assert index.search("history of zanzibar")[0].title == "Zanzibar"


def test_full_name_title_beats_single_word_titles(tmp_path: Path):
    articles = [
        ("Birthday", "A birthday is the anniversary of the birth of a person. " * 5),
        ("Cage", "A cage is an enclosure made of mesh, bars or wires. " * 5),
        ("Nicolas Cage", CAGE),
        ("Pride", "Pride is an inwardly directed emotion about oneself. " * 5),
        ("Pride and Prejudice", "Pride and Prejudice is an 1813 novel by Jane Austen. " * 5),
    ]
    path = tmp_path / "w.sqlite"
    build_index(iter(articles), path)
    with WikiIndex(path) as index:
        assert index.search("What is Nicolas Cage's birthday?")[0].title == "Nicolas Cage"
        assert index.search("Who wrote Pride and Prejudice?")[0].title == "Pride and Prejudice"
        # With no longer title in the question, a lone word's article is fine.
        assert index.search("what is a cage")[0].title == "Cage"
