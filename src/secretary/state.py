"""Local app state (which briefs were handled). Kept outside the synced folder."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from .fileutil import atomic_write_text


class StateStore:
    def __init__(self, path: Path):
        self._path = path
        self._handled: dict[str, str] = {}
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            self._handled = dict(data.get("handled_briefs", {}))

    def is_handled(self, brief_id: str) -> bool:
        return brief_id in self._handled

    def mark_handled(self, brief_id: str, when: dt.datetime | None = None) -> None:
        if brief_id in self._handled:
            return
        when = when or dt.datetime.now().astimezone()
        self._handled[brief_id] = when.replace(microsecond=0).isoformat()
        atomic_write_text(self._path, json.dumps({"handled_briefs": self._handled}, indent=2))
