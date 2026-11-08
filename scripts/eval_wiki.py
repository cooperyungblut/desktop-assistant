"""Check how often the Wikipedia search hands her the answer (no model needed).

    uv run python scripts/eval_wiki.py              # one line per question + totals
    uv run python scripts/eval_wiki.py -v           # also show what she read on a miss
    uv run python scripts/eval_wiki.py --no-rerank  # keyword ranking only
    uv run python scripts/eval_wiki.py --compare --model C:/data/wikipedia/bge-reranker
                                                    # try another reranker model
    uv run python scripts/eval_wiki.py --compare    # keyword vs every rerank mode

Questions live in evals/wiki_questions.yaml, each with the article(s) that
answer it and a pattern for the answer. Uses the index in config.yaml and the
same search, filtering and clipping as the app (see secretary/retrieval_eval.py).
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from secretary.config import load_config
from secretary.rerank import MODES, RerankedWiki, make_reranked
from secretary.retrieval_eval import (
    RetrievalResult,
    load_retrieval_cases,
    run_retrieval_case,
    summary,
)
from secretary.wiki import WikiIndex

ROOT = Path(__file__).resolve().parents[1]
LEGEND = "A = right article among her passages, a = answer text in them, number = rank"


def mark(result: RetrievalResult) -> str:
    if not result.looked_up:
        return "SKIP"
    return ("A" if result.article_ok else "-") + ("a" if result.answer_ok else "-")


def compare(wiki: RerankedWiki, cases) -> None:
    """Every question under keyword ranking and each rerank mode, side by side.
    The model scores each question once; the modes only re-order."""
    names = ["keyword", *MODES]
    results: dict[str, list[RetrievalResult]] = {name: [] for name in names}
    print("  ".join(f"{name:<8}" for name in names) + "  question")
    for case in cases:
        row = []
        results["keyword"].append(run_retrieval_case(wiki.index, case))
        for mode in MODES:
            wiki.mode = mode
            results[mode].append(run_retrieval_case(wiki, case))
        for name in names:
            result = results[name][-1]
            row.append(f"{mark(result)} {result.rank or '':<3}")
        print("  ".join(f"{cell:<8}" for cell in row) + f"  {case.question}")
    print()
    for name in names:
        print(f"{name:<8} {summary(results[name])}")
    print(f"({MODES[0]} times include the model scoring; the other modes reuse its scores)")
    print(LEGEND)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--cases", type=Path, default=ROOT / "evals" / "wiki_questions.yaml")
    parser.add_argument("-v", "--verbose", action="store_true", help="show passages on a miss")
    parser.add_argument(
        "--no-rerank", action="store_true", help="skip wiki.rerank_model, keywords only"
    )
    parser.add_argument(
        "--model", type=Path, help="a reranker model folder instead of wiki.rerank_model"
    )
    parser.add_argument(
        "--compare", action="store_true", help="keyword ranking vs every rerank mode"
    )
    args = parser.parse_args()
    cases = load_retrieval_cases(args.cases)
    results = []
    config = load_config(args.config).wiki
    model = None if args.no_rerank else (args.model or config.rerank_model)
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    with make_reranked(WikiIndex(config.index_file), model, config.rerank_mode) as wiki:
        if isinstance(wiki, RerankedWiki):
            print(f"reranker: on ({model}, mode {wiki.mode})")
        elif args.no_rerank:
            print("reranker: off (--no-rerank)")
        elif model is None:
            print(f"reranker: off (no wiki.rerank_model in {args.config})")
        else:
            print(f"reranker: off ({model} didn't load; see the line above)")
        if args.compare:
            if not isinstance(wiki, RerankedWiki):
                raise SystemExit("--compare needs the reranker loaded")
            compare(wiki, cases)
            return
        for case in cases:
            result = run_retrieval_case(wiki, case)
            results.append(result)
            print(f"{mark(result):<4} {result.rank or '':>2} {result.ms:>6.0f} ms  {case.question}")
            if args.verbose and not (result.article_ok and result.answer_ok):
                print(f"       got: {result.titles}\n       {result.text[:600]}\n")
    print(f"\n{summary(results)}")
    print(LEGEND)


if __name__ == "__main__":
    main()
