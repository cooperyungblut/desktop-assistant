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


def test_rare_terms_drop_common_unknown_and_stay_in_order(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("secretary.wiki.SMALL_INDEX", 0)
    filler = "The history of this place is long and the history matters. "
    articles = [(f"Town {n}", filler * 5) for n in range(200)]
    articles.append(("Zanzibar", "Zanzibar is an archipelago off the coast of Tanzania. " * 5))
    path = tmp_path / "w.sqlite"
    build_index(iter(articles), path)
    with WikiIndex(path) as index:
        assert index._rare_terms(["history", "zanzibar"]) == ["zanzibar"]
        assert index._rare_terms(["histories", "zanzibar"]) == ["zanzibar"]  # stemmed
        assert index._rare_terms(["zanzibar", "qwxyz"]) == ["zanzibar"]  # never seen
        assert index._rare_terms(["history"]) == []  # only common words: no body search
        assert index.search("history of zanzibar")[0].title == "Zanzibar"


def test_small_indexes_never_drop_words(tmp_path: Path):
    path = tmp_path / "w.sqlite"
    build_index(iter(ARTICLES), path)
    with WikiIndex(path) as index:
        assert set(index._rare_terms(["paris", "city", "river"])) == {"paris", "city", "river"}


def test_title_with_more_real_words_beats_a_filler_start(tmp_path: Path):
    articles = [
        ("The Roman", "The Roman is a novel by Mika Waltari published in 1964. " * 4),
        ("Roman Empire", "The Roman Empire was the state ruled by the Romans. " * 4),
        (
            "Fall of the Western Roman Empire",
            "The fall of the Western Roman Empire was a long decline. " * 4,
        ),
    ]
    path = tmp_path / "w.sqlite"
    build_index(iter(articles), path)
    with WikiIndex(path) as index:
        found = index.search("Why did the Roman Empire fall?")
        titles = [p.title for p in found]
        assert titles[0] == "Roman Empire"
        # every key word is in this title, so it is found without a body search
        assert "Fall of the Western Roman Empire" in titles
        assert "The Roman" not in titles[:1]


def test_search_records_stage_timings(tmp_path: Path):
    path = tmp_path / "w.sqlite"
    build_index(iter(ARTICLES), path)
    with WikiIndex(path) as index:
        index.search("What is Nicolas Cage's birthday?")
        stages = [name for name, _ in index.trace]
    assert stages[0] == "exact title" and "word counts" in stages


def test_warm_up_runs_without_touching_the_main_connection(tmp_path: Path):
    path = tmp_path / "w.sqlite"
    build_index(iter(ARTICLES), path)
    with WikiIndex(path) as index:
        assert index.warm_up() >= 0
        assert index.search("Who is Nicolas Cage?")[0].title == "Nicolas Cage"


def test_passages_say_how_they_were_found(tmp_path: Path):
    path = tmp_path / "w.sqlite"
    build_index(iter(ARTICLES), path)
    with WikiIndex(path) as index:
        found = index.search("What is Nicolas Cage's birthday?")
        assert found[0].match == "title"
        by_text = index.search("which river is the capital of France on")
        assert any(p.match == "all" for p in by_text)


def test_title_hit_brings_the_facts_line_after_a_long_opening(tmp_path: Path):
    opening = " ".join(
        f"France is a country in Western Europe with a long history, sentence {n}."
        for n in range(20)
    )
    articles = [
        ("France", f"{opening}\nFacts: Capital: Paris; Currency: Euro.\nHistory goes on here."),
        ("Germany", "Germany is a country in Central Europe with many old cities. " * 5),
    ]
    path = tmp_path / "w.sqlite"
    build_index(iter(articles), path)
    with WikiIndex(path) as wiki:
        found = wiki.search("What is the capital of France?", limit=4)
    assert [p.title for p in found[:2]] == ["France", "France"]
    assert found[0].text.startswith("France is a country")
    assert found[1].text == "Facts: Capital: Paris; Currency: Euro."
    assert found[1].match == "title"


def test_title_hit_without_facts_line_adds_nothing(tmp_path: Path):
    articles = [("Germany", "Germany is a country in Central Europe with many old cities. " * 5)]
    path = tmp_path / "w.sqlite"
    build_index(iter(articles), path)
    with WikiIndex(path) as wiki:
        found = wiki.search("Tell me about Germany", limit=4)
    assert [p.title for p in found] == ["Germany"]
