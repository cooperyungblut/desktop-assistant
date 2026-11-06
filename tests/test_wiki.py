from __future__ import annotations

import sqlite3
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from secretary.wiki import (
    Redirect,
    WikiIndex,
    _phrase_rank,
    _phrases,
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
    assert stages[0] == "exact title" and "word counts" in stages and "body" in stages


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


def test_facts_rows_with_leftover_css_are_left_out(tmp_path: Path):
    css = "Occupations: /* start https://en.wikipedia.org/ */ .mw-parser-output .hlist dl"
    text = (
        "Nicolas Cage is an American actor and film producer known for many roles. " * 9
        + f"\nFacts: Born: January 7, 1964; {css}; Children: 3."
    )
    path = tmp_path / "w.sqlite"
    build_index(iter([("Nicolas Cage", text)]), path)
    with WikiIndex(path) as wiki:
        found = wiki.search("What is Nicolas Cage's birthday?", limit=4)
    assert found[1].text == "Facts: Born: January 7, 1964; Children: 3."


def test_redirect_names_lead_to_the_article(tmp_path: Path):
    paris = "Paris is the capital and largest city of France, on the Seine. " * 4
    items = [
        Redirect("Capital of France", "Paris"),  # may come before its target
        ("Paris", paris),
        Redirect("Paris", "Paris"),  # pointless: not stored
    ]
    path = tmp_path / "w.sqlite"
    stats = build_index(iter(items), path)
    assert stats.redirects == 1
    with WikiIndex(path) as wiki:
        found = wiki.search("What is the capital of France?")
    assert found[0].title == "Paris" and found[0].match == "title"


def test_index_without_redirect_table_still_searches(tmp_path: Path):
    path = tmp_path / "w.sqlite"
    build_index(iter(ARTICLES), path)
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE redirects")
    with WikiIndex(path) as wiki:
        assert wiki.search("Who is Nicolas Cage?")[0].title == "Nicolas Cage"


def test_kind_of_thing_in_the_question_picks_the_bracketed_article(tmp_path: Path):
    books = "Harry Potter is a series of seven fantasy novels written by J. K. Rowling. " * 3
    films = (
        "Harry Potter is a film series based on the novels by J. K. Rowling. "
        "Daniel Radcliffe starred as Harry Potter in all eight films. " * 2
    )
    items = [("Harry Potter", books), ("Harry Potter (film series)", films)]
    path = tmp_path / "w.sqlite"
    build_index(iter(items), path)
    with WikiIndex(path) as wiki:
        movie = wiki.search("Who played the lead role in the Harry Potter movies?")
        book = wiki.search("Who wrote Harry Potter?")
    assert movie[0].title == "Harry Potter (film series)"
    assert book[0].title == "Harry Potter"


def test_named_article_adds_its_passage_matching_the_rest_of_the_question(tmp_path: Path):
    opening = "Ada Lovelace was an English mathematician and writer of the 1800s. " * 9
    later = [
        "She was born in London to the poet Lord Byron and Anne Isabella Milbanke.",
        "Her notes on the Analytical Engine include the first published algorithm "
        "intended to be carried out by a machine, so she is called the first programmer.",
        "She died of uterine cancer in 1852 at the age of 36.",
    ]
    path = tmp_path / "w.sqlite"
    build_index(iter([("Ada Lovelace", "\n".join([opening, *later]))]), path)
    with WikiIndex(path) as wiki:
        found = wiki.search("Why is Ada Lovelace called the first programmer?")
    assert [p.title for p in found] == ["Ada Lovelace", "Ada Lovelace"]
    assert "Analytical Engine" in found[1].text and found[1].match == "title"


def _films_index(tmp_path: Path) -> Path:
    def text(sentence: str) -> str:
        return (sentence + " ") * 4

    items = [
        ("The Matrix", text("The Matrix is a 1999 film that stars Keanu Reeves as Neo.")),
        ("Matrix", text("A matrix is a rectangular array of numbers in mathematics.")),
        ("Played", text("Played is a 2006 crime film set in London's underworld.")),
        ("Starred", text("Starred is a 2021 album by a pop singer from Sweden.")),
        ("Role", text("A role is a set of behaviours expected of a person.")),
        ("Four species", text("The four species are plants used during the Sukkot holiday.")),
        (
            "StarCraft",
            "StarCraft is a science fiction media franchise created by Chris Metzen. " * 9
            + "\nIt features four species: the Terrans, the Zerg, the Protoss and the Xel'Naga.",
        ),
    ]
    path = tmp_path / "w.sqlite"
    build_index(iter(items), path)
    return path


@pytest.mark.parametrize(
    "question",
    [
        "Who played the main role in The Matrix?",
        "Who starred in The Matrix?",
        "Who played the lead role in The Matrix?",
    ],
)
def test_capitalised_names_beat_plain_words(tmp_path: Path, question: str):
    with WikiIndex(_films_index(tmp_path)) as wiki:
        assert wiki.search(question)[0].title == "The Matrix"


def test_capitalised_name_beats_a_longer_plain_title(tmp_path: Path):
    with WikiIndex(_films_index(tmp_path)) as wiki:
        found = wiki.search("What are the four species in Starcraft?", limit=4)
    # The named article leads; the text search may add others after it.
    assert found[0].title == "StarCraft" and found[0].match == "title"
    assert any("Zerg" in p.text for p in found if p.title == "StarCraft")
    assert all(p.match != "title" for p in found if p.title != "StarCraft")


def test_all_lowercase_questions_still_find_titles(tmp_path: Path):
    with WikiIndex(_films_index(tmp_path)) as wiki:
        assert wiki.search("who starred in the matrix")[0].title == "The Matrix"


def test_answer_named_nowhere_in_the_question_is_found_by_its_text(tmp_path: Path):
    def text(sentence: str) -> str:
        return (sentence + " ") * 4

    items = [
        ("Africa", text("Africa is the second-largest continent, home to many countries.")),
        ("Nile", text("The Nile is the longest river in Africa, flowing north to the sea.")),
        ("Congo River", text("The Congo River is the deepest river in the world.")),
        ("Sahara", text("The Sahara is a desert covering much of North Africa.")),
        # a title full of the question's words: must not beat the Nile's text
        ("N3 (South Africa)", text("The N3 is the longest national route in South Africa.")),
    ]
    path = tmp_path / "w.sqlite"
    build_index(iter(items), path)
    with WikiIndex(path) as wiki:
        found = wiki.search("What is the longest river in Africa?", limit=8)
    titles = [p.title for p in found]
    assert titles[0] == "Africa"  # the title match still leads
    assert "Nile" in titles[:3]  # and the passage that answers it is right behind
    assert titles.index("Nile") < titles.index("N3 (South Africa)")


def test_a_lone_capital_letter_is_a_search_word(tmp_path: Path):
    def text(sentence: str) -> str:
        return (sentence + " ") * 4

    items = [
        ("Chemical symbol", text("A chemical symbol is a short code for a chemical element.")),
        ("Sodium", text("Sodium is a chemical element; it has the symbol Na.")),
        ("Potassium", text("Potassium is a chemical element; it has the symbol K.")),
        ("Calcium", text("Calcium is a chemical element; it has the symbol Ca.")),
    ]
    path = tmp_path / "w.sqlite"
    build_index(iter(items), path)
    with WikiIndex(path) as wiki:
        found = wiki.search("Which element has the chemical symbol K?", limit=4)
    assert "Potassium" in [p.title for p in found[:2]]
    assert "k" in query_terms("Which element has the chemical symbol K?")
    assert "i" not in query_terms("Do I know what A means?")


def test_openings_holding_every_word_beat_short_stray_mentions(tmp_path: Path):
    filler = " ".join(f"Other fact number {n} about this subject." for n in range(12))
    items = [
        (
            "Photosynthesis",
            "Photosynthesis is a system of biological processes by which most plants "
            "convert light energy, usually from sunlight, into chemical energy. "
            + filler
            + "\\nLater sections cover leaves and chloroplasts in detail. " * 3,
        ),
        (
            "Nile",
            "The Nile is a major north-flowing river in northeastern Africa. It is the "
            "longest river in Africa and flows into the Mediterranean Sea. " + filler,
        ),
        # Short passages deep in other articles that happen to hold the words.
        (
            "Namibia Premier League",
            "Intro. " * 40 + "\\nA river club from Africa won the longest final.",
        ),
        (
            "Thermal energy",
            "Intro. " * 40 + "\\nPlants convert light energy into chemical energy too.",
        ),
        ("Chemical energy", "Chemical energy is energy stored in chemical bonds. " * 5),
    ]
    path = tmp_path / "w.sqlite"
    build_index(iter(items), path)
    with WikiIndex(path) as wiki:
        river = wiki.search("What is the longest river in Africa?", limit=4)
        plants = wiki.search(
            "What is the name of the process by which plants convert light energy "
            "into chemical energy?",
            limit=4,
        )
    assert "Nile" in [p.title for p in river[:3]]
    assert "Photosynthesis" in [p.title for p in plants[:3]]


def test_openings_stage_only_returns_first_passages(tmp_path: Path):
    items = [
        ("Nile", "The Nile is the longest river in Africa. " * 6),
        ("Rally", "Intro text for the rally. " * 30 + "\nThe longest river stage in Africa."),
    ]
    path = tmp_path / "w.sqlite"
    build_index(iter(items), path)
    with WikiIndex(path) as wiki:
        found = wiki._ranked(
            ["longest", "river", "africa"], 5, require_all=True, openings_only=True
        )
        stages = (wiki.search("What is the longest river in Africa?"), wiki.trace)[1]
    assert [p.title for p in found] == ["Nile"]
    assert "openings" in [name for name, _ in stages]


def test_openings_that_say_it_the_way_the_question_does_come_first(tmp_path: Path):
    items = [
        ("Africa", "Africa is the second-largest continent. " * 5),
        ("Orange River", "The Orange River is the longest river in South Africa. " * 5),
        ("Nile", "The Nile is the longest river in Africa. It flows north. " * 5),
    ]
    path = tmp_path / "w.sqlite"
    build_index(iter(items), path)
    with WikiIndex(path) as wiki:
        ranked = wiki._ranked

        def short_first(terms, limit, *, require_all, openings_only=False):
            # On the full index BM25 puts short "South Africa" openings ahead.
            found = ranked(terms, limit, require_all=require_all, openings_only=openings_only)
            return sorted(found, key=lambda p: p.title != "Orange River")

        wiki._ranked = short_first
        found = wiki.search("What is the longest river in Africa?", limit=4)
    assert [p.title for p in found if p.match != "title"][0] == "Nile"
    phrases = _phrases("What is the longest river in Africa?", ["longest", "river", "africa"])
    assert [
        _phrase_rank(text, phrases)
        for text in (
            "The Nile is the longest river in Africa.",
            "The Zambezi is the fourth-longest river in Africa.",
            "The Orange is the longest river in South Africa.",
        )
    ] == [0, 1, 2]
    assert _phrases("Who is Keanu?", ["keanu"]) == []


def test_the_answer_beats_runners_up_and_portal_pages(tmp_path: Path):
    items = [
        ("Africa", "Africa is the second-largest continent. " * 5),
        ("Zambezi", "The Zambezi is the fourth-longest river in Africa. " * 5),
        ("Portal:Rivers/Selected article/3", "The longest river in Africa flows north. " * 5),
        ("Nile", "The Nile is the longest river in Africa. It flows north. " * 5),
    ]
    path = tmp_path / "w.sqlite"
    build_index(iter(items), path)
    with WikiIndex(path) as wiki:
        ranked = wiki._ranked

        def runners_up_first(terms, limit, *, require_all, openings_only=False):
            # On the full index BM25 puts the Zambezi ahead.
            found = ranked(terms, limit, require_all=require_all, openings_only=openings_only)
            return sorted(found, key=lambda p: p.title != "Zambezi")

        wiki._ranked = runners_up_first
        found = wiki.search("What is the longest river in Africa?", limit=4)
        stray = ranked(["longest", "river", "africa"], 9, require_all=True)
    assert [p.title for p in found if p.match != "title"][0] == "Nile"
    assert not any(p.title.startswith("Portal:") for p in stray)


def test_loose_matches_are_skipped_when_not_wanted(tmp_path: Path):
    items = [("Cage", "A cage is an enclosure for animals. " * 8)]
    path = tmp_path / "w.sqlite"
    build_index(iter(items), path)
    with WikiIndex(path) as wiki:
        wiki.search("Which birds live in a cage at the zoo?", loose=False)
        strict = [name for name, _ in wiki.trace]
        wiki.search("Which birds live in a cage at the zoo?")
        loose = [name for name, _ in wiki.trace]
    assert strict.count("body") == 1 and loose.count("body") == 2


def test_the_thing_a_plain_title_is_for_is_looked_up_too(tmp_path: Path):
    items = [
        ("Chemical symbol", "A chemical symbol is a short code for a chemical element. " * 5),
        ("Salt", "Salt is a mineral made mostly of sodium chloride (NaCl). " * 5),
        ("Symbol", "A symbol is a mark or sign that stands for an idea. " * 5),
        ("List of copper salts", "Copper salts are compounds of copper. " * 5),
    ]
    path = tmp_path / "w.sqlite"
    build_index(iter(items), path)
    with WikiIndex(path) as wiki:
        found = wiki.search("What is the chemical symbol for salt?", limit=8)
    titles = [p.title for p in found if p.match == "title"]
    assert titles[0] == "Chemical symbol" and "Salt" in titles
    assert "Symbol" not in titles


def test_openings_drop_trailing_qualifiers_and_the_x_is_not_a_name(tmp_path: Path):
    items = [
        ("Take Care (album)", "Take Care is an album with the song The Largest. " * 5),
        Redirect("The largest", "Take Care (album)"),
        (
            "Sahara",
            "The Sahara is the largest hot desert in the world by area, covering much "
            "of North Africa. " * 3,
        ),
        ("Polar desert", "Polar deserts are cold, dry regions near the poles. " * 5),
    ]
    path = tmp_path / "w.sqlite"
    build_index(iter(items), path)
    with WikiIndex(path) as wiki:
        found = wiki.search(
            "What is the largest desert by area in the world, excluding polar deserts?",
            limit=8,
            loose=False,
        )
    titles = [p.title for p in found]
    assert "Take Care (album)" not in titles
    assert "Sahara" in titles


def test_facts_line_comes_second_when_it_fits_the_question(tmp_path: Path):
    opening = "The Isle of Man is a self-governing island in the Irish Sea. " * 9
    text = (
        opening
        + "\nFacts: Motto: Quocunque jeceris stabit; Capital: Douglas."
        + "\nIts official parliament is called Tynwald, one of the oldest. " * 12
    )
    path = tmp_path / "w.sqlite"
    build_index(iter([("Isle of Man", text)]), path)
    with WikiIndex(path) as wiki:
        found = wiki.search("What is the official motto of the Isle of Man?", limit=8)
        laws = wiki.search("What is the parliament of the Isle of Man called?", limit=8)
    assert "Motto" in found[1].text
    assert "Tynwald" in laws[1].text


def test_name_with_a_lowercase_second_word_beats_a_shorter_one(tmp_path: Path):
    items = [
        ("The Bhutanese", "The Bhutanese is a newspaper based in Bhutan. " * 5),
        ("Bhutanese ngultrum", "The ngultrum is the currency of Bhutan, code BTN. " * 5),
    ]
    path = tmp_path / "w.sqlite"
    build_index(iter(items), path)
    with WikiIndex(path) as wiki:
        found = wiki.search("What is the currency code for the Bhutanese ngultrum?", limit=8)
    assert found[0].title == "Bhutanese ngultrum"


def test_facts_rows_the_question_asks_about_come_first(tmp_path: Path):
    opening = "The Isle of Man is a self-governing island in the Irish Sea. " * 9
    rows = "; ".join(f"Row {n}: value number {n}" for n in range(40))
    text = opening + f"\nFacts: {rows}; Motto: Quocunque jeceris stabit." + "\nMore. " * 3
    path = tmp_path / "w.sqlite"
    build_index(iter([("Isle of Man", text)]), path)
    with WikiIndex(path) as wiki:
        found = wiki.search("What is the motto of the Isle of Man?", limit=8)
    assert found[1].text.startswith("Facts: Motto: Quocunque")


def test_second_slot_adds_what_the_opening_lacks(tmp_path: Path):
    opening = "Ganesha is a Hindu god, one of the best-known deities in Hinduism. " * 8
    text = (
        opening
        + "\nFacts: Abode: Kailasha with his parents; Mount: Mouse; Consort: Buddhi and Siddhi."
        + "\nGanesha is a Hindu god mentioned in many texts over the centuries. " * 9
    )
    path = tmp_path / "w.sqlite"
    build_index(iter([("Ganesha", text)]), path)
    with WikiIndex(path) as wiki:
        found = wiki.search("What is the mount of the Hindu god Ganesha?", limit=8)
    assert "Mount: Mouse" in found[1].text
