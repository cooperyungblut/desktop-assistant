"""Build the offline Wikipedia search index (one time, a few hours at most).

    uv sync --extra wiki
    uv run python scripts/build_wiki_index.py                # the whole thing
    uv run python scripts/build_wiki_index.py --limit 20000  # quick trial run
    uv run python scripts/build_wiki_index.py --ask "Nicolas Cage birthday"

Paths come from the `wiki:` section of config.yaml. The index is written beside
its final name and renamed when finished, so stopping it midway is harmless.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from secretary.config import load_config
from secretary.rerank import make_reranked
from secretary.wiki import BuildStats, WikiIndex, build_index, iter_articles


def ask(
    index_file: Path, question: str, rerank_model: Path | None = None, mode: str = "fuse"
) -> None:
    with make_reranked(WikiIndex(index_file), rerank_model, mode) as index:
        started = time.perf_counter()
        # The same search the app runs for a question (not an explicit "look up").
        passages = index.search(question, 8, loose=False)
        took = time.perf_counter() - started
        stages = ", ".join(f"{name} {ms:.0f}" for name, ms in index.trace)
    for passage in passages:
        print(f"\n[{passage.title}] ({passage.match}) {passage.url}\n{passage.text}")
    print(f"\n{len(passages)} passages in {took * 1000:.0f} ms (at ms: {stages})")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--limit", type=int, help="index only the first N articles")
    parser.add_argument("--workers", type=int, help="processes for reading a ZIM (default: config)")
    parser.add_argument("--ask", metavar="QUESTION", help="search an existing index and exit")
    args = parser.parse_args()
    wiki = load_config(args.config).wiki
    if args.ask:
        ask(wiki.index_file, args.ask, wiki.rerank_model, wiki.rerank_mode)
        return
    if not wiki.source.exists():
        sys.exit(f"wiki.source not found: {wiki.source} (see docs/wikipedia.md)")
    started = time.perf_counter()

    def progress(stats: BuildStats) -> None:
        minutes = (time.perf_counter() - started) / 60
        print(
            f"{stats.articles:>9,} articles, {stats.passages:>10,} passages, "
            f"{stats.skipped:,} skipped, {minutes:.1f} min",
            flush=True,
        )

    stats = build_index(
        iter_articles(wiki.source, workers=args.workers or wiki.workers),
        wiki.index_file,
        max_chars_per_article=wiki.max_chars_per_article,
        limit=args.limit,
        progress=progress,
    )
    size_gb = wiki.index_file.stat().st_size / 1e9
    print(
        f"Done: {stats.articles:,} articles, {stats.redirects:,} redirects, "
        f"{size_gb:.1f} GB at {wiki.index_file}"
    )


if __name__ == "__main__":
    main()
