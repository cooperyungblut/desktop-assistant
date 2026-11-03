"""Entry point: `python -m secretary` (or `desk-secretary`)."""

from __future__ import annotations

import argparse
import logging
import sys
import webbrowser
from pathlib import Path

import uvicorn

from .config import load_config
from .server import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the desk secretary.")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--no-browser", action="store_true", help="don't open the UI")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # Quiet chatty libraries: one line per HTTP request, and espeak's harmless
    # "words count mismatch" warnings on every sentence.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # phonemizer resets its logger's level whenever kokoro creates a backend, so a
    # level set here doesn't stick; a filter does.
    logging.getLogger("phonemizer").addFilter(
        lambda record: "words count mismatch" not in record.getMessage()
    )
    if not args.config.is_file():
        sys.exit(f"{args.config} not found. Copy config.example.yaml to config.yaml first.")

    config = load_config(args.config)
    app = create_app(config)
    url = f"http://{config.host}:{config.port}/"
    if not args.no_browser:
        webbrowser.open(url)
    uvicorn.run(app, host=config.host, port=config.port, log_level="warning")


if __name__ == "__main__":
    main()
