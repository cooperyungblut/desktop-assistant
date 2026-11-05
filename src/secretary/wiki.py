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

import functools
import re
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

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

    @property
    def url(self) -> str:
        return "https://en.wikipedia.org/wiki/" + self.title.replace(" ", "_")


def split_passages(text: str, limit: int = PASSAGE_CHARS) -> list[str]:
    """Pack paragraphs into passages of up to about `limit` characters.

    A paragraph longer than the limit is cut at sentence ends. The first passage
    is always the article's opening.
    """
    pieces: list[str] = []
    for para in re.split(r"\n\s*\n|\n", text):
        para = para.strip()
        if not para:
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


# A word in more than this share of passages (history, war, new) barely helps find
# anything and makes BM25 score millions of rows, so it is left out of the query
# whenever a rarer word is there to search with.
COMMON_SHARE = 0.01
COMMON_FLOOR = 1000  # never prune in a small index


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
CREATE VIRTUAL TABLE passages USING fts5(
    title, body, article_id UNINDEXED, seq UNINDEXED,
    tokenize = "{TOKENIZER}",
    detail = column
);
CREATE VIRTUAL TABLE passage_terms USING fts5vocab(passages, row);
"""


@functools.lru_cache(maxsize=1024)
def _stem(word: str) -> str:
    """How the index tokenizer spells a word (porter stem), asked of SQLite itself."""
    scratch = sqlite3.connect(":memory:")
    try:
        scratch.execute(f"CREATE VIRTUAL TABLE s USING fts5(w, tokenize = '{TOKENIZER}')")
        scratch.execute("INSERT INTO s (w) VALUES (?)", (word,))
        scratch.execute("CREATE VIRTUAL TABLE v USING fts5vocab(s, row)")
        row = scratch.execute("SELECT term FROM v").fetchone()
        return row[0] if row else word
    finally:
        scratch.close()


def iter_articles(source_dir: Path, batch_size: int = 2000) -> Iterator[tuple[str, str]]:
    """Yield (title, text) from every parquet file in `source_dir`, in order."""
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


@dataclass
class BuildStats:
    articles: int = 0
    skipped: int = 0
    passages: int = 0


def build_index(
    articles: Iterator[tuple[str, str]],
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
        for title, text in articles:
            if limit is not None and stats.articles >= limit:
                break
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
        self._db = sqlite3.connect(f"file:{index_file.as_posix()}?mode=ro", uri=True)

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> WikiIndex:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def search(self, question: str, limit: int = 3) -> list[Passage]:
        """The best passages for a question, best first.

        An article whose title the question names outright comes first (its
        opening passage), then the best BM25 matches. Titles count much more than
        body text. All-words matches are tried before any-word matches.
        """
        terms = self._prune_common(query_terms(question))
        if not terms:
            return []
        found: list[Passage] = []
        seen: set[tuple[str, str]] = set()

        def add(passage: Passage) -> None:
            key = (passage.title, passage.text)
            if key not in seen and len(found) < limit:
                seen.add(key)
                found.append(passage)

        for passage in self._title_hits(question):
            add(passage)
        for require_all in (True, False):
            for passage in self._ranked(terms, limit, require_all=require_all):
                add(passage)
            if len(found) >= limit:
                break
        return found

    def _prune_common(self, terms: list[str]) -> list[str]:
        """Drop unknown words and words that appear in a large share of passages."""
        if len(terms) < 2:
            return terms
        total = self._db.execute("SELECT max(id) FROM passages_docsize").fetchone()[0] or 0
        counts: dict[str, int] = {}
        for term in terms:
            # The index stores stems, so look up the stem's count.
            row = self._db.execute(
                "SELECT doc FROM passage_terms WHERE term = ?", (_stem(term),)
            ).fetchone()
            counts[term] = row[0] if row else 0
        # A word the index has never seen (a misspelling) can't match anything.
        known = [t for t in terms if counts[t] > 0] or terms
        limit = max(total * COMMON_SHARE, COMMON_FLOOR)
        rare = [t for t in known if counts[t] <= limit]
        return rare or [min(known, key=lambda t: counts[t])]

    def _title_hits(self, question: str) -> list[Passage]:
        """Openings of articles whose exact title appears in the question."""
        words = _WORD.findall(question.lower().replace("'s", ""))
        candidates = {
            " ".join(words[i:j])
            for i in range(len(words))
            for j in range(i + 1, min(i + 5, len(words)) + 1)
        }
        # Single stopwords ("it", "what") are real titles but never what was meant.
        candidates = {c for c in candidates if c not in _STOPWORDS and len(c) > 2}
        hits: list[Passage] = []
        for candidate in sorted(candidates, key=len, reverse=True):
            row = self._db.execute(
                "SELECT p.title, p.body FROM articles a"
                " JOIN passages p ON p.rowid = a.first_passage"
                " WHERE a.title = ? COLLATE NOCASE LIMIT 1",
                (candidate,),
            ).fetchone()
            if row:
                hits.append(Passage(row[0], row[1], score=100.0))
                if len(hits) >= 2:
                    break
        return hits

    def _ranked(self, terms: list[str], limit: int, *, require_all: bool) -> list[Passage]:
        query = _fts_query(terms, require_all=require_all)
        rows = self._db.execute(
            # bm25 weights: title 10x the body. Lower (more negative) is better.
            "SELECT title, body, bm25(passages, 10.0, 1.0) AS rank FROM passages"
            " WHERE passages MATCH ? ORDER BY rank LIMIT ?",
            (query, limit),
        ).fetchall()
        return [Passage(title, body, score=-rank) for title, body, rank in rows]
