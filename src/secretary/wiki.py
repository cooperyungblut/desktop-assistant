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


def query_terms(question: str) -> list[str]:
    """The words of a question that are worth searching for."""
    words = _WORD.findall(question.lower().replace("'s", ""))
    return [w for w in words if w not in _STOPWORDS and len(w) > 1]


# A word in more than this share of passages (history, war, france) barely helps
# find anything and would make BM25 score millions of rows, so the body search
# leaves it out and works from the rarer words. The share is estimated from a few
# slices of the index, because counting a common word exactly is slow.
COMMON_SHARE = 0.02
SAMPLE_PASSAGES = 100_000  # per slice
SAMPLE_SLICES = (0.2, 0.5, 0.8)
SMALL_INDEX = 50_000  # below this many passages every word counts as rare
MAX_BODY_TERMS = 3
# The infobox line written by the ZIM loader, and how many passages in to look for it.
FACTS_PREFIX = "Facts: "
FACTS_WITHIN = 8


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
    max_chars_per_article: int = 4000,
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

    def __enter__(self) -> WikiIndex:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _total(self) -> int:
        if self._passages is None:
            row = self._db.execute("SELECT max(id) FROM passages_docsize").fetchone()
            self._passages = row[0] or 0
        return self._passages

    def search(self, question: str, limit: int = 3) -> list[Passage]:
        """The best passages for a question, best first.

        In order: the opening and Facts line of an article whose exact title is in
        the question; articles whose title holds all the key words; then, only when
        no title matched exactly, passages holding all the rarer key words, then
        passages holding any of them. `self.trace` keeps the time
        each stage took, for tuning.
        """
        started = time.perf_counter()
        trace: list[tuple[str, float]] = []
        terms = list(dict.fromkeys(query_terms(question)))
        found: list[Passage] = []
        seen: set[tuple[str, str]] = set()

        def add(passages: list[Passage], stage: str) -> None:
            trace.append((stage, (time.perf_counter() - started) * 1000))
            for passage in passages:
                key = (passage.title, passage.text)
                if key not in seen and len(found) < limit:
                    seen.add(key)
                    found.append(passage)

        try:
            if not terms:
                return []
            add(self._title_hits(question), "exact title")
            named = bool(found)
            if len(terms) >= 2 and len(found) < limit:
                add(self._title_words(terms, limit), "title words")
            if named:
                # The question names an article: its opening and facts are the answer.
                # A body search can take seconds on a cold disk and only adds noise.
                return found
            rare = self._rare_terms(terms)
            trace.append(("word counts", (time.perf_counter() - started) * 1000))
            for require_all in (True, False):
                if rare and len(found) < limit:
                    add(self._ranked(rare, limit, require_all=require_all), "body")
            return found
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

        Titles with more real words win ("Pride and Prejudice" over "Pride"), then
        ones that don't start or end with a filler word ("Roman Empire" over "The
        Roman"), then the longest. A word is used once. Titles of a single real
        word count only when no longer title was found.
        """
        words = _WORD.findall(question.lower().replace("'s", ""))
        spans = []
        for i in range(len(words)):
            for j in range(i + 1, min(i + 6, len(words)) + 1):
                chunk = words[i:j]
                content = sum(1 for w in chunk if w not in _STOPWORDS)
                if not content or len(" ".join(chunk)) <= 2:
                    continue
                edge = (chunk[0] in _STOPWORDS) + (chunk[-1] in _STOPWORDS)
                spans.append((content, edge, i, j))
        spans.sort(key=lambda span: (-span[0], span[1], span[2] - span[3], span[2]))
        used: set[int] = set()
        hits: list[Passage] = []
        for content, _edge, i, j in spans:
            if content == 1 and hits:
                break  # a longer title was found: skip the lone words
            if used & set(range(i, j)):
                continue
            row = self._article(" ".join(words[i:j]))
            if row:
                used.update(range(i, j))
                hits.append(Passage(row[0], row[1], score=100.0, match="title"))
                facts = self._facts(row[2], row[3])
                if facts and facts not in row[1]:
                    hits.append(Passage(row[0], facts, score=99.0, match="title"))
                if len(hits) >= 3:
                    break
        return hits

    def _article(self, name: str) -> tuple[str, str, int, int] | None:
        """(title, opening, id, first passage) of the article called `name`,
        directly or through a redirect ("Nicholas Cage", "Capital of France")."""
        row = self._db.execute(
            "SELECT p.title, p.body, a.id, a.first_passage FROM articles a"
            " JOIN passages p ON p.rowid = a.first_passage"
            " WHERE a.title = ? COLLATE NOCASE LIMIT 1",
            (name,),
        ).fetchone()
        if row or not self._has_redirects:
            return row
        return self._db.execute(
            "SELECT p.title, p.body, a.id, a.first_passage FROM redirects r"
            " JOIN articles a ON a.title = r.target COLLATE NOCASE"
            " JOIN passages p ON p.rowid = a.first_passage"
            " WHERE r.title = ? COLLATE NOCASE LIMIT 1",
            (name,),
        ).fetchone()

    def _facts(self, article_id: int, first_passage: int) -> str | None:
        """The article's "Facts: ..." line (its infobox: born, capital, ...), if any.

        It sits right after the opening paragraphs, so it is often not in the first
        passage. An article's passages have neighbouring rowids, so this is a few
        rowid lookups, not a search.
        """
        rows = self._db.execute(
            "SELECT body FROM passages WHERE rowid BETWEEN ? AND ? AND article_id = ?"
            " ORDER BY rowid",
            (first_passage, first_passage + FACTS_WITHIN, article_id),
        ).fetchall()
        for (body,) in rows:
            at = body.find(FACTS_PREFIX)
            if at >= 0:
                line = body[at + len(FACTS_PREFIX) :].split("\n", 1)[0]
                # Indexes built before the loader dropped <style> blocks have CSS in
                # some rows ("/* start ... */ .mw-parser-output ..."): leave those out.
                rows = [r for r in line.split("; ") if "mw-parser-output" not in r]
                return FACTS_PREFIX + "; ".join(rows) if rows else None
        return None

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

    def _ranked(self, terms: list[str], limit: int, *, require_all: bool) -> list[Passage]:
        query = _fts_query(terms, require_all=require_all)
        rows = self._db.execute(
            # bm25 weights: title 10x the body. Lower (more negative) is better.
            "SELECT title, body, bm25(passages, 10.0, 1.0) AS rank FROM passages"
            " WHERE passages MATCH ? ORDER BY rank LIMIT ?",
            (query, limit),
        ).fetchall()
        match = "all" if require_all else "any"
        return [Passage(title, body, score=-rank, match=match) for title, body, rank in rows]

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
