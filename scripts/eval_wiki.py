"""Check how often the Wikipedia search hands her the answer (no model needed).

    uv run python scripts/eval_wiki.py        # one line per question + totals
    uv run python scripts/eval_wiki.py -v     # also show what she read on a miss
    uv run python scripts/eval_wiki.py --no-rerank   # keyword ranking only

Questions live in evals/wiki_questions.yaml, each with the article(s) that
answer it and a pattern for the answer. Uses the index in config.yaml and the
same search, filtering and clipping as the app (see secretary/retrieval_eval.py).
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from secretary.config import load_config
from secretary.rerank import RerankedWiki, make_reranked
from secretary.retrieval_eval import load_retrieval_cases, run_retrieval_case, summary
from secretary.wiki import WikiIndex

ROOT = Path(__file__).resolve().parents[1]


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
    args = parser.parse_args()
    cases = load_retrieval_cases(args.cases)
    results = []
    config = load_config(args.config).wiki
    model = None if args.no_rerank else config.rerank_model
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    with make_reranked(WikiIndex(config.index_file), model) as wiki:
        if isinstance(wiki, RerankedWiki):
            print(f"reranker: on ({model})")
        elif args.no_rerank:
            print("reranker: off (--no-rerank)")
        elif model is None:
            print(f"reranker: off (no wiki.rerank_model in {args.config})")
        else:
            print(f"reranker: off ({model} didn't load; see the line above)")
        for case in cases:
            result = run_retrieval_case(wiki, case)
            results.append(result)
            if not result.looked_up:
                mark = "SKIP"
            else:
                mark = ("A" if result.article_ok else "-") + ("a" if result.answer_ok else "-")
            print(f"{mark:<4} {result.rank or '':>2} {result.ms:>6.0f} ms  {case.question}")
            if args.verbose and not (result.article_ok and result.answer_ok):
                print(f"       got: {result.titles}\n       {result.text[:600]}\n")
    print(f"\n{summary(results)}")
    print("A = right article among her passages, a = answer text in them, number = rank")


if __name__ == "__main__":
    main()
