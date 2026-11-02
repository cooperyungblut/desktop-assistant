"""Compare local chat models on fact accuracy, honesty, mood tags and speed.

    uv run python scripts/eval_models.py                      # the model in config.yaml
    uv run python scripts/eval_models.py gemma4:12b qwen3.5:9b --runs 3 -v

Each model answers every question in evals/cases.yaml (about the made-up
sample briefs) using Juniper's real prompt, `--runs` times each because
answers vary at temperature 0.7. Then it prints one row per model:

    facts    answers with every required fact and nothing changed or invented
    unknown  questions the brief can't answer where she said she didn't know
    tag      replies that opened with a valid mood tag
    mood     replies whose mood fit (e.g. not happy about a rejection)
    first    median seconds to the first word (the model is loaded first)
    total    median seconds for the whole reply

-v lists every failed check with the answer, so you can see whether a miss is
a real mistake or a check that's too strict. Full results are saved as JSON in
data/evals/. Models must already be pulled (ollama pull <name>).
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path

from secretary.briefs import load_brief
from secretary.config import AppConfig, load_config
from secretary.evaluation import Summary, failures, load_cases, run_case, table
from secretary.llm import LlmUnavailable, OllamaClient
from secretary.persona import load_persona

ROOT = Path(__file__).resolve().parents[1]


async def evaluate(config: AppConfig, models: list[str], runs: int, verbose: bool) -> None:
    persona = load_persona(config.persona_file)
    cases = load_cases(ROOT / "evals" / "cases.yaml")
    briefs = {
        b: load_brief(ROOT / "samples" / "briefs" / f"{b}.md") for b in {c.brief for c in cases}
    }
    options = persona.sampling.as_options()
    summaries: list[Summary] = []

    for name in models:
        client = OllamaClient(config.ollama.model_copy(update={"model": name}))
        try:
            if not await client.is_available():
                print(
                    f"\n{name}: not available. Is Ollama running, and did you `ollama pull {name}`?"
                )
                continue
            print(f"\n{name}: loading...", flush=True)
            await client.warm_up(options)  # so load time isn't counted as speed
            results = []
            for run in range(runs):
                for case in cases:
                    r = await run_case(client, persona, briefs[case.brief], case)
                    results.append(r)
                    mark = "." if r.facts_ok and r.mood_ok else "x"
                    print(mark, end="", flush=True)
                if runs > 1:
                    print(f"  run {run + 1}/{runs}", flush=True)
            print()
            summary = Summary(name, results)
            summaries.append(summary)
            if verbose:
                print("\n".join(failures(summary)) or "  no failed checks")
        except LlmUnavailable as exc:
            print(f"\n{name}: {exc}")
        finally:
            await client.aclose()

    if not summaries:
        sys.exit("No model could be evaluated.")
    print("\n" + table(summaries))
    if not verbose:
        print("\nAdd -v to see each failed check and the answer behind it.")
    save(summaries, runs)


def save(summaries: list[Summary], runs: int) -> None:
    out_dir = ROOT / "data" / "evals"  # git-ignored
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"eval-{dt.datetime.now():%Y%m%d-%H%M%S}.json"
    data = {
        "runs": runs,
        "models": [
            {
                "model": s.model,
                "facts": s.facts,
                "unknown": s.unknowns,
                "tag": s.tags,
                "mood": s.moods,
                "median_first_token_s": s.median("first_token_s"),
                "median_total_s": s.median("total_s"),
                "results": [asdict(r) for r in s.results],
            }
            for s in summaries
        ],
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Saved {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("models", nargs="*", help="Ollama model tags (default: the one in config)")
    parser.add_argument("--runs", type=int, default=2, help="times to ask each question")
    parser.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true", help="list failed checks")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    if not args.config.is_file():
        sys.exit(f"{args.config} not found. Copy config.example.yaml to config.yaml first.")
    config = load_config(args.config)
    models = args.models or [config.ollama.model]
    asyncio.run(evaluate(config, models, max(1, args.runs), args.verbose))


if __name__ == "__main__":
    main()
