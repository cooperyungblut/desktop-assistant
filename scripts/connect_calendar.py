"""Connect Google Calendar once, so she can add events.

    uv run python scripts/connect_calendar.py            # sign in (opens the browser)
    uv run python scripts/connect_calendar.py --check    # is the saved sign-in still good?

Before the first run, set `calendar.engine: google` in config.yaml and save the
OAuth client from Google Cloud as data/google-oauth-client.json; the steps are
in docs/calendar.md. The sign-in is saved to data/google-token.json (git-ignored).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import webbrowser
from pathlib import Path

from secretary.config import load_config
from secretary.google_calendar import CalendarError, GoogleCalendar, authorize


async def check(calendar: GoogleCalendar) -> None:
    try:
        name = await calendar.calendar_name()
    finally:
        await calendar.aclose()
    print(f"Connected: she can add events to the calendar {name!r}.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--check", action="store_true", help="only test the saved sign-in")
    args = parser.parse_args()
    config = load_config(args.config).calendar
    if config.engine != "google":
        sys.exit("Set calendar.engine: google in config.yaml first (see docs/calendar.md).")
    if not config.client_file.is_file():
        sys.exit(f"{config.client_file} not found. Download the OAuth client (docs/calendar.md).")
    try:
        if not args.check:
            print("Opening Google's sign-in page in your browser...")

            def open_browser(url: str) -> None:
                if not webbrowser.open(url):
                    print(f"Open this link to sign in:\n{url}")

            authorize(config, open_browser)
            print(f"Signed in. Saved to {config.token_file}")
        asyncio.run(check(GoogleCalendar(config)))
    except CalendarError as exc:
        sys.exit(f"Calendar: {exc}")


if __name__ == "__main__":
    main()
