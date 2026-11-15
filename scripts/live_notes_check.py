"""Try live notes without the app: list audio devices, or listen for a while.

    uv run python scripts/live_notes_check.py --devices
    uv run python scripts/live_notes_check.py                 # listen for 60 s
    uv run python scripts/live_notes_check.py --seconds 30 --no-computer

Listens to your microphone ("You") and to what the computer plays
("Computer"), using the live_notes and stt settings in config.yaml, and writes
the transcript to a page in your notes folder ("Live notes check ...") while
printing each line. Play a video or join a test call to hear both sides.
Needs: uv sync --extra live --extra stt (plus your other extras).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime as dt
import logging
import sys
from pathlib import Path

from secretary.config import load_config
from secretary.live_audio import list_devices
from secretary.live_notes import LiveNotesUnavailable, LiveSession, make_sources, page_name
from secretary.notes import Notebook, page_file_name
from secretary.stt import make_transcriber


async def listen(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    live = config.live_notes.model_copy(
        update={"computer_audio": not args.no_computer, "microphone": not args.no_mic}
    )
    print(f"Loading Whisper {config.stt.model} on {config.stt.device}...")
    transcriber = make_transcriber(config.stt)
    await transcriber.load()
    if not transcriber.available:
        sys.exit(f"Whisper didn't load: {getattr(transcriber, 'error', 'stt.engine is none')}")

    async def show(line, label):
        print(f"  {label}: {line.text}")

    notebook = Notebook(config.notes)
    name = page_name("Live notes check {date} {time}", dt.datetime.now())
    page = notebook.dir / page_file_name(name)
    session = LiveSession(live, transcriber, notebook, make_sources(live), on_line=show)
    try:
        await session.start(page)
    except LiveNotesUnavailable as exc:
        sys.exit(str(exc))
    for source in session._sources:
        if source.source in session.listening:
            print(f"Listening to {source.source}: {getattr(source, 'name', '?')}")
    for problem in session.problems:
        print(f"  (problem) {problem}")
    print(f"For {args.seconds} s. Ctrl+C stops early. Writing to {page}")
    try:
        await asyncio.sleep(args.seconds)
    except asyncio.CancelledError:
        pass
    finally:
        await session.stop()
    for problem in session.problems:
        print(f"  (problem) {problem}")
    print(f"Done: {len(session.lines)} lines in {page}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--devices", action="store_true", help="list audio devices and exit")
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--no-computer", action="store_true", help="microphone only")
    parser.add_argument("--no-mic", action="store_true", help="computer audio only")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if args.devices:
        try:
            devices = list_devices()
        except Exception as exc:
            sys.exit(f"Couldn't list devices ({exc}). Did you run uv sync --extra live?")
        for kind, names in devices.items():
            print(f"{kind}:")
            for n in names:
                print(f"  {n}")
        return
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(listen(args))


if __name__ == "__main__":
    main()
