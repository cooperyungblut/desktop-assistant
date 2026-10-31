"""Copy a fake sample brief into the dev inbox, as if the Drive sync just delivered it.

    python scripts/drop_sample.py              # morning brief, dated today
    python scripts/drop_sample.py afternoon
    python scripts/drop_sample.py evening --inbox "C:/Users/you/My Drive/Secretary"

The copy gets today's date and a fresh id, so it shows up as a new, unhandled brief.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DATE = "2026-11-02"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "type", nargs="?", default="morning", choices=["morning", "afternoon", "evening"]
    )
    parser.add_argument("--inbox", type=Path, default=ROOT / "data" / "inbox")
    args = parser.parse_args()

    source = ROOT / "samples" / "briefs" / f"{SAMPLE_DATE}_{args.type}.md"
    now = dt.datetime.now().astimezone().replace(microsecond=0)
    today = now.date().isoformat()
    text = source.read_text(encoding="utf-8").replace(SAMPLE_DATE, today)
    text = re.sub(
        r"^generated_at: .*$", f"generated_at: {now.isoformat()}", text, count=1, flags=re.MULTILINE
    )

    target_dir = args.inbox / "briefs"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{today}_{args.type}.md"
    target.write_text(text, encoding="utf-8")
    print(f"Dropped {target}")


if __name__ == "__main__":
    main()
