"""Check that the chat model can see pictures, without the app or the avatar.

    uv run python scripts/try_vision.py screenshot.png
    uv run python scripts/try_vision.py photo.jpg "What breed is this dog?"

Sends the picture (PNG or JPEG) with the question to the model in config.yaml,
using the same prompt the app builds, and prints her reply and Ollama's timing
line. If the model has no vision, Ollama's error is printed instead. Watch
VRAM in Task Manager while it runs: the vision part loads with the model.

The app shrinks pictures to about 1,600 px before sending; this script sends
the file as it is, so a large photo takes longer here than in the app.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import logging
import sys
import time
from pathlib import Path

from secretary.config import load_config
from secretary.images import Picture, picture_mime
from secretary.llm import LlmUnavailable, OllamaClient
from secretary.persona import load_persona
from secretary.prompt import build_messages

ROOT = Path(__file__).resolve().parents[1]


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("picture", type=Path)
    ap.add_argument("question", nargs="?", default="What do you see?")
    ap.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    args = ap.parse_args()

    data = args.picture.read_bytes()
    mime = picture_mime(data)
    if mime is None:
        print("That file isn't a PNG or JPEG.")
        return 1
    picture = Picture(base64.b64encode(data).decode("ascii"), mime)

    config = load_config(args.config)
    persona = load_persona(config.persona_file)
    model = OllamaClient(config.ollama)
    messages = build_messages(persona, None, [], args.question, pictures=[picture])
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    print(f"{config.ollama.model}, {len(data) / 1024:.0f} KB {mime}: {args.question}\n")
    start = time.perf_counter()
    first = None
    try:
        async for token in model.stream_chat(messages, persona.sampling.as_options()):
            first = first or time.perf_counter() - start
            print(token, end="", flush=True)
    except LlmUnavailable as exc:
        print(f"Ollama refused it: {exc}")
        return 1
    finally:
        await model.aclose()
    print(f"\n\nFirst token after {first or 0:.1f} s, done in {time.perf_counter() - start:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
