"""Check how often the Wikipedia search hands her the answer (no model needed).

    uv run python scripts/eval_wiki.py        # one line per question + totals
    uv run python scripts/eval_wiki.py -v     # also show what she read on a miss

Questions live in evals/wiki_questions.yaml, each with the article(s) that
answer it and a pattern for the answer. Uses the index in config.yaml and the
same search, filtering and clipping as the app (see secretary/retrieval_eval.py).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from secretary.config import load_config
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
    args = parser.parse_args()
    cases = load_retrieval_cases(args.cases)
    results = []
    with WikiIndex(load_config(args.config).wiki.index_file) as wiki:
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
