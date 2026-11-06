"""Offline Wikipedia lookup: build a small search index once, then query it.

Nothing here knows about the chat model, the persona or the web UI, so another
front end can reuse it as is. The index is one SQLite file (full-text search,
BM25 ranking) that runs on CPU and disk, so it needs no VRAM. Searching uses only
the standard library; building it needs pyarrow to read the parquet files
(`uv sync --extra wiki`).

The source is the cleaned English dump on Hugging Face (`wikimedia/wikipedia`):
plain article text with a `title` and a `text` column. Each article becomes a few
short passages of about 600 characters, the opening one first, because that is
where definitions and dates live. The query is also matched against titles, so
a misspelled first name still finds the right page.
"""

from __future__ import annotations

import contextlib
import re
import sqlite3
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

PASSAGE_CHARS = 600
MIN_ARTICLE_CHARS = 200

# Words that never help find an article. Kept short on purpose: BM25 already
# down-weights common words, this just keeps the query from being all noise.
_STOPWORDS = frozenset(
    """a an the of in on at to for from by with and or but is are was were be been am do does
    did what whats who whos whom when where why how which that this these those it its his her
    their my your our me you i we they he she tell about please can could would should will
    know many much s""".split()  # noqa: SIM905
)
_WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Passage:
    title: str
    text: str
    score: float  # higher is better
    # How it was found: "title" (the question names the article), "all" (every
    # key word is in the passage) or "any" (only some are: weak).
    match: str = "any"

    @property
    def url(self) -> str:
        return "https://en.wikipedia.org/wiki/" + self.title.replace(" ", "_")


def is_fragment(line: str) -> bool:
    """A section heading or list label: short, no sentence ending, not prose.

    The cleaned dump keeps headings as bare lines ("Early life", "Plot"). They
    carry no facts and only take room in her prompt.
    """
    return len(line.split()) < 8 and not line.endswith((".", "!", "?", '"', ")", ":"))


def split_passages(text: str, limit: int = PASSAGE_CHARS) -> list[str]:
    """Pack paragraphs into passages of up to about `limit` characters.

    A paragraph longer than the limit is cut at sentence ends. Bare headings are
    dropped. The first passage is the article's opening.
    """
    pieces: list[str] = []
    for para in re.split(r"\n\s*\n|\n", text):
        para = para.strip()
        if not para or is_fragment(para):
            continue
        if len(para) <= limit:
            pieces.append(para)
            continue
        current = ""
        for sentence in re.split(r"(?<=[.!?])\s+", para):
            if current and len(current) + len(sentence) + 1 > limit:
                pieces.append(current)
                current = ""
            current = f"{current} {sentence}".strip()
        if current:
            pieces.append(current)
    passages: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + len(piece) + 1 > limit:
            passages.append(current)
            current = ""
        current = f"{current}\n{piece}".strip()
    if current:
        passages.append(current)
    return passages


def is_indexable(title: str, text: str) -> bool:
    """Skip stubs and disambiguation pages: nothing useful to say from them."""
    if len(text) < MIN_ARTICLE_CHARS:
        return False
    return not re.search(r"\b(may|can) (also )?refer to\b", text[:300])


def _facts_line(bodies: list[str]) -> str | None:
    """The "Facts: ..." line (the infobox: born, capital, ...) among an article's
    passages. It comes right after the first paragraph of the opening."""
    for body in bodies:
        at = body.find(FACTS_PREFIX)
        if at >= 0:
            line = body[at + len(FACTS_PREFIX) :].split("\n", 1)[0]
            # Indexes built before the loader dropped <style> blocks have CSS in
            # some rows ("/* start ... */ .mw-parser-output ..."): leave those out.
            rows = [r for r in line.split("; ") if "mw-parser-output" not in r]
            return FACTS_PREFIX + "; ".join(rows) if rows else None
    return None


def _stem(word: str) -> str:
    """A rough stem, enough to match "played" with "plays" or "movies" with "movie"."""
    for suffix, keep in (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)] + keep
    return word


def _mentions(stems: set[str], wanted: str) -> bool:
    """True if a passage's word stems include `wanted`, or a longer form of it
    ("marri" from "married" also finds "marriage")."""
    if wanted in stems:
        return True
    return len(wanted) >= 4 and any(stem.startswith(wanted) for stem in stems)


def _take_turns(first: list[Passage], second: list[Passage], limit: int) -> list[Passage]:
    """Two from `first` (a named article's opening and best passage), then one from
    each list in turn, without repeats."""
    order = first[:2]
    rest = first[2:]
    for i in range(max(len(rest), len(second))):
        order += second[i : i + 1] + rest[i : i + 1]
    found: list[Passage] = []
    seen: set[tuple[str, str]] = set()
    for passage in order:
        key = (passage.title, passage.text)
        if key not in seen and len(found) < limit:
            seen.add(key)
            found.append(passage)
    return found


def _phrases(question: str, terms: list[str]) -> list[re.Pattern[str]]:
    """Patterns for the question's run of words from its first key word to its
    last, strictest first: with the word before it when that's "the" or "a"
    ("the longest river in africa", which "the fourth-longest river in Africa"
    misses), then the bare run ("longest river in africa"). Empty if the run is
    under two key words or too long to expect word for word."""
    words = _WORD.findall(question.lower().replace("'s", ""))
    places = [i for i, w in enumerate(words) if w in terms]
    if len(places) < 2 or places[-1] - places[0] >= PHRASE_WORDS:
        return []
    start, end = places[0], places[-1] + 1
    runs = [words[start:end]]
    if start > 0 and words[start - 1] in ("the", "a", "an"):
        runs.insert(0, words[start - 1 : end])
    return [re.compile(r"\b" + r"\W+".join(map(re.escape, run)) + r"\b") for run in runs]


def _phrase_rank(text: str, phrases: list[re.Pattern[str]]) -> int:
    """0 for the strictest phrase found in `text`, 1 for the next, ...; len if none."""
    lowered = text.lower()
    return next((k for k, phrase in enumerate(phrases) if phrase.search(lowered)), len(phrases))


def query_terms(question: str) -> list[str]:
    """The words of a question that are worth searching for. A lone capital
    letter counts ("the chemical symbol K"), but not "I" or "A"."""
    words = _WORD.findall(question.lower().replace("'s", ""))
    letters = {m.lower() for m in re.findall(r"\b([B-HJ-Z])\b", question)}
    return [w for w in words if w not in _STOPWORDS and (len(w) > 1 or w in letters)]


# A word in more than this share of passages (history, war, france) barely helps
# find anything and would make BM25 score millions of rows, so the body search
# leaves it out and works from the rarer words. The share is estimated from a few
# slices of the index, because counting a common word exactly is slow.
COMMON_SHARE = 0.02
SAMPLE_PASSAGES = 100_000  # per slice
SAMPLE_SLICES = (0.2, 0.5, 0.8)
SMALL_INDEX = 50_000  # below this many passages every word counts as rare
MAX_BODY_TERMS = 3
# Article openings are searched with every key word (up to this many), common or not.
MAX_OPENING_TERMS = 6
# Words that shape the question but won't be in the answer's text ("what is the
# name of the process ..."): left out when every word must match.
_QUESTION_FILLER = frozenset(
    ["name", "names", "called", "known", "kind", "type", "sort", "word", "term"]
)
# The infobox line written by the ZIM loader, and how many passages in to look for it.
FACTS_PREFIX = "Facts: "
# How many passages of one article to read when looking inside it. Generous: an
# article holds about max_chars_per_article / 600 of them (40 at 24,000).
ARTICLE_PASSAGES = 60
# Openings fetched for the all-key-words stage; those that hold the question's
# words in the question's order go first. Longest run of words tried as a phrase.
OPENING_POOL = 300
# Key words the openings stage may drop, one at a time, when nothing has them all.
MAX_DROPPED_TERMS = 3
# Wikipedia pages that aren't articles ("Portal:Malawi/Featured article/3").
_NOT_ARTICLES = ("Portal:", "Wikipedia:", "Template:", "Category:", "Help:", "File:", "Draft:")
PHRASE_WORDS = 8
# Words in a question that point at one of several same-named articles.
_FILM = ("film series", "film", "franchise")
_QUALIFIERS: dict[str, tuple[str, ...]] = {
    "movie": _FILM,
    "movies": _FILM,
    "film": _FILM,
    "films": _FILM,
    "show": ("TV series",),
    "tv": ("TV series",),
    "series": ("TV series", "film series"),
    "episode": ("TV series",),
    "episodes": ("TV series",),
    "book": ("novel", "book"),
    "novel": ("novel",),
    "song": ("song",),
    "album": ("album",),
    "band": ("band",),
    "game": ("video game",),
    "play": ("play",),
    "musical": ("musical",),
}


def _fts_query(terms: list[str], *, require_all: bool) -> str:
    quoted = [f'"{t}"' for t in terms]
    return (" AND " if require_all else " OR ").join(quoted)


TOKENIZER = "porter unicode61 remove_diacritics 2"
_SCHEMA = f"""
CREATE TABLE articles (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    first_passage INTEGER NOT NULL  -- rowid of the opening passage
);
CREATE TABLE redirects (
    title TEXT NOT NULL,   -- the other name
    target TEXT NOT NULL   -- the article's real title
);
CREATE VIRTUAL TABLE passages USING fts5(
    title, body, article_id UNINDEXED, seq UNINDEXED,
    tokenize = "{TOKENIZER}",
    detail = column
);
"""


def iter_articles(source: Path, workers: int = 1) -> Iterator[tuple[str, str] | Redirect]:
    """Yield (title, text) from a Kiwix .zim file or a folder of parquet files.
    A ZIM file also yields its redirects."""
    if source.suffix.lower() == ".zim":
        from secretary.wiki_zim import iter_zim_articles

        return iter_zim_articles(source, workers)
    return _iter_parquet(source)


def _iter_parquet(source_dir: Path, batch_size: int = 2000) -> Iterator[tuple[str, str]]:
    """The cleaned Hugging Face dump: a `title` and a `text` column per article."""
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - message only
        raise RuntimeError("Building the index needs pyarrow: uv sync --extra wiki") from exc
    files = sorted(source_dir.glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"No .parquet files in {source_dir}")
    for path in files:
        for batch in pq.ParquetFile(path).iter_batches(
            batch_size=batch_size, columns=["title", "text"]
        ):
            columns = batch.to_pydict()
            yield from zip(columns["title"], columns["text"], strict=True)


class Redirect(NamedTuple):
    """Another name for an article ("Nicholas Cage" -> "Nicolas Cage").

    A source may yield these between its (title, text) articles."""

    title: str
    target: str


@dataclass
class BuildStats:
    articles: int = 0
    skipped: int = 0
    passages: int = 0
    redirects: int = 0


def build_index(
    articles: Iterator[tuple[str, str] | Redirect],
    index_file: Path,
    *,
    max_chars_per_article: int = 24_000,
    limit: int | None = None,
    progress: Callable[[BuildStats], None] | None = None,
) -> BuildStats:
    """Write a fresh index to `index_file` (replacing any old one).

    It is built beside the target and renamed at the end, so a stopped or
    failed build never leaves a half-written index behind.
    """
    index_file.parent.mkdir(parents=True, exist_ok=True)
    temp = index_file.with_name(index_file.name + ".building")
    temp.unlink(missing_ok=True)
    stats = BuildStats()
    db = sqlite3.connect(temp)
    try:
        # A rebuild can always start over, so trade crash safety for speed.
        db.execute("PRAGMA journal_mode = OFF")
        db.execute("PRAGMA synchronous = OFF")
        db.executescript(_SCHEMA)
        for item in articles:
            if limit is not None and stats.articles >= limit:
                break
            if isinstance(item, Redirect):
                if item.title and item.target and item.title.lower() != item.target.lower():
                    db.execute("INSERT INTO redirects VALUES (?, ?)", item)
                    stats.redirects += 1
                continue
            title, text = item
            if not title or not text or not is_indexable(title, text):
                stats.skipped += 1
                continue
            parts = split_passages(text[:max_chars_per_article])
            if not parts:
                stats.skipped += 1
                continue
            article_id = stats.articles + 1
            first = None
            for seq, part in enumerate(parts):
                cursor = db.execute(
                    "INSERT INTO passages (title, body, article_id, seq) VALUES (?, ?, ?, ?)",
                    (title, part, article_id, seq),
                )
                first = first if first is not None else cursor.lastrowid
            db.execute(
                "INSERT INTO articles (id, title, first_passage) VALUES (?, ?, ?)",
                (article_id, title, first),
            )
            stats.articles += 1
            stats.passages += len(parts)
            if stats.articles % 50_000 == 0:
                db.commit()
                if progress:
                    progress(stats)
        db.execute("CREATE INDEX articles_title ON articles (title COLLATE NOCASE)")
        db.execute("CREATE INDEX redirects_title ON redirects (title COLLATE NOCASE)")
        db.execute("INSERT INTO passages (passages) VALUES ('optimize')")
        db.commit()
    except BaseException:
        db.close()
        temp.unlink(missing_ok=True)
        raise
    db.close()
    temp.replace(index_file)
    if progress:
        progress(stats)
    return stats


class WikiIndex:
    """Read-only handle on a built index."""

    def __init__(self, index_file: Path):
        if not index_file.is_file():
            raise FileNotFoundError(
                f"No Wikipedia index at {index_file}. Build it first (docs/wikipedia.md)."
            )
        self._path = index_file
        self._passages: int | None = None
        self.trace: list[tuple[str, float]] = []
        # Read-only, so it is safe to search from a worker thread.
        self._db = sqlite3.connect(
            f"file:{index_file.as_posix()}?mode=ro", uri=True, check_same_thread=False
        )
        # Indexes built before redirects were stored don't have the table.
        self._has_redirects = bool(
            self._db.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'redirects'"
            ).fetchone()
        )

    def close(self) -> None:
        self._db.close()

    def interrupt(self) -> None:
        """Stop a search running in another thread (one that took too long), so it
        doesn't hold up the next question."""
        self._db.interrupt()

    def __enter__(self) -> WikiIndex:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _total(self) -> int:
        if self._passages is None:
            row = self._db.execute("SELECT max(id) FROM passages_docsize").fetchone()
            self._passages = row[0] or 0
        return self._passages

    def search(self, question: str, limit: int = 3, *, loose: bool = True) -> list[Passage]:
        """The best passages for a question, best first.

        Two kinds of match, taken in turns so neither crowds out the other:
        - by title: the opening, Facts line and best-matching passage of an article
          the question names, then articles whose title holds all the key words;
        - by text: passages holding all the rarer key words, then (if `loose`) any
          of them. That last stage is slow on a full index (seconds: "nicholas OR
          cage OR birthday" matches millions of passages), so leave it off unless
          those matches will be used.
        The text search matters when the answer is a name the question doesn't
        say ("the longest river in Africa" -> Nile, while "Africa" is the title
        match). `self.trace` keeps the time each stage took, for tuning.
        """
        started = time.perf_counter()
        trace: list[tuple[str, float]] = []
        terms = list(dict.fromkeys(query_terms(question)))

        def mark(stage: str) -> None:
            trace.append((stage, (time.perf_counter() - started) * 1000))

        try:
            if not terms:
                return []
            by_title = self._title_hits(question)
            mark("exact title")
            if len(terms) >= 2:
                by_title += self._title_words(terms, limit)
                mark("title words")
            rare = self._rare_terms(terms)
            mark("word counts")
            named = {p.title for p in by_title}
            opening_terms = [t for t in terms if t not in _QUESTION_FILLER][:MAX_OPENING_TERMS]
            # Openings that hold every key word: an article's first lines define
            # it ("Photosynthesis is ... by which plants convert light energy into
            # chemical energy"), so they beat a stray mention deep in some page.
            # Among those, the ones that say it the way the question does ("the
            # longest river in Africa", not "the longest river in South Africa")
            # come first.
            # No opening has them all ("largest desert by area in the world,
            # excluding polar deserts")? Drop key words from the end, where the
            # qualifiers usually are, down to two.
            openings: list[Passage] = []
            for keep in range(len(opening_terms), 1, -1):
                found = self._ranked(
                    opening_terms[:keep], OPENING_POOL, require_all=True, openings_only=True
                )
                openings = [p for p in found if p.title not in named]
                if openings or keep <= len(opening_terms) - MAX_DROPPED_TERMS:
                    break
            phrases = _phrases(question, opening_terms)
            if phrases:
                openings.sort(key=lambda p: _phrase_rank(p.text, phrases))
            by_text = openings[:limit]
            mark("openings")
            for require_all in (True, False) if loose else (True,):
                if rare and len(by_text) < limit:
                    found = self._ranked(rare, limit * 3, require_all=require_all)
                    # Other articles only: the named ones are already in by_title.
                    by_text += [p for p in found if p.title not in named][:limit]
                    mark("body")
            return _take_turns(by_title, by_text, limit)
        finally:
            self.trace = trace

    def _share(self, term: str) -> float:
        """Roughly the share of passages that contain the word (0: never seen)."""
        total = self._total()
        match = f'"{term}"'
        if total <= SAMPLE_PASSAGES * len(SAMPLE_SLICES):
            hits = self._db.execute(
                "SELECT count(*) FROM passages WHERE passages MATCH ?", (match,)
            ).fetchone()[0]
            return hits / max(total, 1)
        hits = 0
        for slice_at in SAMPLE_SLICES:
            low = int(total * slice_at)
            hits += self._db.execute(
                "SELECT count(*) FROM passages WHERE passages MATCH ? AND rowid > ? AND rowid <= ?",
                (match, low, low + SAMPLE_PASSAGES),
            ).fetchone()[0]
        return hits / (SAMPLE_PASSAGES * len(SAMPLE_SLICES))

    def _rare_terms(self, terms: list[str]) -> list[str]:
        """The words worth searching passages for: seen in the index, rarest first.

        Words that are in a large share of passages are left out, and so are words
        the index has never seen (a misspelling can't match anything).
        """
        shares = {term: self._share(term) for term in terms}
        known = sorted((t for t in terms if shares[t] > 0), key=lambda t: shares[t])
        if self._total() <= SMALL_INDEX:
            return known[:MAX_BODY_TERMS]
        return [t for t in known if shares[t] <= COMMON_SHARE][:MAX_BODY_TERMS]

    def _title_hits(self, question: str) -> list[Passage]:
        """Openings of articles whose exact title appears in the question.

        Names written with capitals win ("Keanu Reeves", "The Matrix", "StarCraft"
        over "four species" or "played"), then titles with more real words
        ("Pride and Prejudice" over "Pride"), then ones that don't start or end
        with a filler word ("Roman Empire" over "Roman Empire fall"; a leading
        "the" is fine), then the longest. A word is used once. Once a name is
        found, plain lowercase words are not tried, and without capitals a single
        word only counts when no longer title was found.
        """
        text = question.replace("'s", "").replace("\u2019s", "")
        lowered = text.lower()
        matches = list(_WORD.finditer(lowered))
        words = [m.group() for m in matches]
        # Did the user capitalise it? The first word doesn't count (sentence start).
        capital = [k > 0 and text[m.start()].isupper() for k, m in enumerate(matches)]
        spans = []
        for i in range(len(words)):
            for j in range(i + 1, min(i + 6, len(words)) + 1):
                chunk = words[i:j]
                content = [k for k in range(i, j) if words[k] not in _STOPWORDS]
                if not content or len(" ".join(chunk)) <= 2:
                    continue
                lettered = [k for k in content if not words[k].isdigit()]
                # A name starts with a capital; later words may be lowercase
                # ("Bhutanese ngultrum", "Bengal tiger").
                named = bool(lettered) and capital[lettered[0]]
                edge = (chunk[0] in _STOPWORDS and chunk[0] != "the") + (chunk[-1] in _STOPWORDS)
                spans.append((named, len(content), edge, i, j))
        spans.sort(key=lambda s: (not s[0], -s[1], s[2], s[3] - s[4], s[3]))
        any_named = any(span[0] for span in spans)
        used: set[int] = set()
        hits: list[Passage] = []
        for named, content, _edge, i, j in spans:
            # A name was found: skip plain words and lone words. Except the thing a
            # plain title is "of" or "for": "the chemical symbol for salt" wants
            # "Salt" as well as "Chemical symbol".
            subject = not any_named and i > 0 and words[i - 1] in ("of", "for")
            if hits and not named and (any_named or content == 1) and not subject:
                continue
            if used & set(range(i, j)):
                continue
            name = " ".join(words[i:j])
            others = [
                w
                for k, w in enumerate(words)
                if (k < i or k >= j) and w not in _STOPWORDS and len(w) > 1
            ]
            # One plain word reaches only a real article, not a redirect: "the
            # largest" redirects to an album with a song of that name.
            row = self._qualified(name, others) or self._article(
                name, redirects=named or content > 1
            )
            if row:
                used.update(range(i, j))
                hits += self._article_passages(row, others)
                if len({p.title for p in hits}) >= 2:
                    break
        return hits

    def _qualified(self, name: str, others: list[str]) -> tuple[str, str, int, int] | None:
        """ "Harry Potter" + "movies" -> the article "Harry Potter (film series)".

        Wikipedia tells same-named articles apart with a word in brackets. When the
        question names the kind of thing, try those titles first.
        """
        for word in others:
            for qualifier in _QUALIFIERS.get(word, ()):
                if row := self._article(f"{name} ({qualifier})"):
                    return row
        return None

    def _article_passages(self, row: tuple[str, str, int, int], others: list[str]) -> list[Passage]:
        """For a named article: its opening, then its Facts line and the passage that
        best matches the rest of the question ("who played the lead role"), the
        better fit of those two first."""
        title, opening, article_id, first = row
        found = [Passage(title, opening, score=100.0, match="title")]
        bodies = [
            body
            for (body,) in self._db.execute(
                "SELECT body FROM passages WHERE rowid BETWEEN ? AND ? AND article_id = ?"
                " ORDER BY rowid",
                (first, first + ARTICLE_PASSAGES, article_id),
            )
        ]
        wanted = {_stem(w) for w in others}
        in_opening = {_stem(w) for w in _WORD.findall(opening.lower())}

        def matches(text: str) -> int:
            """Question words the text holds. A word the opening already has
            ("Hindu god" for Ganesha) counts half as much as one it doesn't
            ("mount"): the second slot should add what the opening lacks."""
            stems = {_stem(w) for w in _WORD.findall(text.lower())}
            return sum(1 if _mentions(in_opening, w) else 2 for w in wanted if _mentions(stems, w))

        best, best_score = None, 0
        for body in bodies[1:]:
            score = matches(body)
            if score > best_score and FACTS_PREFIX not in body:
                best, best_score = body, score
        facts = _facts_line(bodies)
        if facts and facts in opening:
            facts = None
        if facts:
            # A long Facts line is cut for her prompt: put the rows the question
            # asks about ("Motto: ...") first so they survive.
            rows = facts[len(FACTS_PREFIX) :].split("; ")
            rows.sort(key=lambda row: -matches(row))
            facts = FACTS_PREFIX + "; ".join(rows)
        # Second place (an article gets two of her slots when others match too)
        # goes to whichever fits the question better: the Facts line for "motto"
        # or "currency code", the best passage for "who played the lead".
        extra = [(matches(facts) if facts else 0, facts), (best_score, best)]
        extra.sort(key=lambda pair: -pair[0])  # stable: the Facts line wins ties
        for score, text in zip((99.0, 98.0), (t for _, t in extra if t is not None), strict=False):
            found.append(Passage(title, text, score=score, match="title"))
        return found

    def _article(self, name: str, redirects: bool = True) -> tuple[str, str, int, int] | None:
        """(title, opening, id, first passage) of the article called `name`,
        directly or through a redirect ("Nicholas Cage", "Capital of France")."""
        row = self._db.execute(
            "SELECT p.title, p.body, a.id, a.first_passage FROM articles a"
            " JOIN passages p ON p.rowid = a.first_passage"
            " WHERE a.title = ? COLLATE NOCASE LIMIT 1",
            (name,),
        ).fetchone()
        if row or not redirects or not self._has_redirects:
            return row
        return self._db.execute(
            "SELECT p.title, p.body, a.id, a.first_passage FROM redirects r"
            " JOIN articles a ON a.title = r.target COLLATE NOCASE"
            " JOIN passages p ON p.rowid = a.first_passage"
            " WHERE r.title = ? COLLATE NOCASE LIMIT 1",
            (name,),
        ).fetchone()

    def _title_words(self, terms: list[str], limit: int) -> list[Passage]:
        """Openings of articles whose title holds every key word ("Fall of the
        Western Roman Empire" for roman, empire, fall). Cheap: only titles match."""
        query = "title : (" + " AND ".join(f'"{t}"' for t in terms) + ")"
        rows = self._db.execute(
            "SELECT title, body, bm25(passages, 10.0, 1.0) AS rank FROM passages"
            " WHERE passages MATCH ? AND seq = 0 ORDER BY rank LIMIT ?",
            (query, limit),
        ).fetchall()
        return [Passage(title, body, score=-rank, match="all") for title, body, rank in rows]

    def _ranked(
        self, terms: list[str], limit: int, *, require_all: bool, openings_only: bool = False
    ) -> list[Passage]:
        """Passages whose text holds the words. Titles don't count here: the title
        stages already cover them, and a title word ("N3 (South Africa)") would
        otherwise beat the passage that answers ("the Nile ... longest river in
        Africa")."""
        query = "body : (" + _fts_query(terms, require_all=require_all) + ")"
        rows = self._db.execute(
            # Lower (more negative) bm25 is better.
            "SELECT title, body, bm25(passages, 0.0, 1.0) AS rank FROM passages"
            " WHERE passages MATCH ?"
            + (" AND seq = 0" if openings_only else "")
            + " ORDER BY rank LIMIT ?",
            (query, limit),
        ).fetchall()
        match = "all" if require_all else "any"
        return [
            Passage(title, body, score=-rank, match=match)
            for title, body, rank in rows
            if not title.startswith(_NOT_ARTICLES)
        ]

    def warm_up(self) -> float:
        """Run a few typical searches so the first real question isn't slow.

        A cold 20 GB index takes tens of seconds to read from disk the first time.
        This uses its own connection, so a real question never waits behind it.
        Returns the seconds it took.
        """
        started = time.perf_counter()
        other = WikiIndex(self._path)
        try:
            for question in WARM_UP_QUESTIONS:
                with contextlib.suppress(sqlite3.Error):
                    other.search(question)
        finally:
            other.close()
        return time.perf_counter() - started


WARM_UP_QUESTIONS = (
    "who was the first president of the united states",
    "what is the capital of australia",
    "why did the roman empire fall",
    "how tall is mount everest",
    "when was the eiffel tower built",
    "who wrote the novel pride and prejudice",
)


def make_wiki(config) -> WikiIndex | None:
    """The index for `wiki:` in config.yaml, or None when it is off or not built yet."""
    import logging

    log = logging.getLogger(__name__)
    if config.engine == "none":
        return None
    if config.engine != "local":
        raise ValueError(f"unknown wiki.engine {config.engine!r}; use local or none")
    try:
        index = WikiIndex(config.index_file)
    except FileNotFoundError as exc:
        log.warning("Wikipedia lookup is on but has no index: %s", exc)
        return None
    log.info("Wikipedia lookup ready (%s)", config.index_file)
    if getattr(config, "warm_up", False):
        import threading

        def warm() -> None:
            seconds = index.warm_up()
            log.info("Wikipedia index warmed up in %.0f s", seconds)

        threading.Thread(target=warm, name="wiki-warm-up", daemon=True).start()
    return index
