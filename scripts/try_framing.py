"""Preview her framing lines around a brief, without the app or the avatar.

    uv run python scripts/try_framing.py                          # all sample briefs
    uv run python scripts/try_framing.py --brief 2026-11-02_morning --runs 3
    uv run python scripts/try_framing.py --persona personas/other.yaml

Prints the readout as she'd say it: her lines marked with ">", the brief's
paragraphs shortened, and any line the fact checks dropped (with the reason).
--raw also prints exactly what the model wrote. Handy for comparing personas:
same brief, different reactions.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

from secretary.briefs import load_brief
from secretary.config import load_config
from secretary.framing import BriefFramer, paragraphs
from secretary.llm import OllamaClient
from secretary.persona import load_persona

ROOT = Path(__file__).resolve().parents[1]


class _Dropped(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if record.levelno >= logging.WARNING:
            print(f"   ({record.levelname.lower()}) {msg}")
            if record.exc_info:
                import traceback

                traceback.print_exception(*record.exc_info)
        for word in ("dropped", "ignored"):
            if f"Framing: {word} " in msg:
                print(f"   ({word}) " + msg.split(f"{word} ", 1)[1])


async def preview(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    persona = load_persona(args.persona or config.persona_file)
    framing = config.brief_framing.model_copy(update={"wait_seconds": 60})  # no rush here
    names = (
        [args.brief]
        if args.brief
        else sorted(p.stem for p in (ROOT / "samples/briefs").glob("*.md"))
    )
    client = OllamaClient(config.ollama)
    if not await client.is_available():
        sys.exit(f"{config.ollama.model} isn't available. Is Ollama running?")
    await client.warm_up(persona.sampling.as_options())
    try:
        for name in names:
            brief = load_brief(ROOT / "samples" / "briefs" / f"{name}.md")
            for run in range(args.runs):
                print(f"\n== {name} ({persona.name}, run {run + 1}) ==")
                start = time.perf_counter()
                framer = BriefFramer(client, persona, brief, framing)
                framer.start()
                opener = await framer.opener()
                first = time.perf_counter() - start
                await framer.closer()
                f = framer.framing
                if args.raw:
                    print("-- what the model wrote --\n" + framer.raw.strip() + "\n--")
                if opener:
                    print(f"> [{opener.mood}] {opener.text}")
                for n, p in enumerate(paragraphs(brief.spoken), 1):
                    print(f"  {p[:70]}...")
                    if a := f.asides.get(n):
                        print(f"> [{a.mood}] {a.text}")
                if f.closer:
                    print(f"> [{f.closer.mood}] {f.closer.text}")
                total = time.perf_counter() - start
                print(f"  opener ready after {first:.1f} s, all after {total:.1f} s")
    finally:
        await client.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--brief", help="sample brief id, e.g. 2026-11-02_morning")
    parser.add_argument("--persona", type=Path, help="persona file (default: the one in config)")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--raw", action="store_true", help="also print the model's raw output")
    parser.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    log = logging.getLogger("secretary.framing")
    log.setLevel(logging.INFO)
    log.addHandler(_Dropped())
    log.propagate = False
    asyncio.run(preview(args))


if __name__ == "__main__":
    main()
