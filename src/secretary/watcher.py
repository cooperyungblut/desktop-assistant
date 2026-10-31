"""Find new brief files by polling the synced folder.

Why polling instead of file-system events (watchdog): Google Drive for Desktop
in "stream" mode serves files from a virtual drive that doesn't reliably raise
change notifications, and even in "mirror" mode the sync client writes files in
stages. Briefs arrive three times a day, so a 5-second poll costs nothing and
is completely predictable. A file that fails to parse (often because it's
half-synced) is retried on the next poll, not marked as seen.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from .briefs import Brief, BriefError, load_brief

log = logging.getLogger(__name__)


@dataclass
class BriefScanner:
    directory: Path
    _seen: dict[Path, tuple[float, int]] = field(default_factory=dict)
    _bad: dict[Path, tuple[float, int]] = field(default_factory=dict)

    def scan(self) -> list[Brief]:
        """Return briefs that are new or changed since the last scan, oldest first."""
        if not self.directory.is_dir():
            return []
        found: list[Brief] = []
        for path in sorted(self.directory.glob("*.md")):
            if path.name.startswith("."):
                continue  # temp files
            try:
                st = path.stat()
            except OSError:
                continue
            sig = (st.st_mtime, st.st_size)
            if self._seen.get(path) == sig:
                continue
            try:
                brief = load_brief(path)
            except BriefError as exc:
                if self._bad.get(path) != sig:  # log each broken version once
                    log.warning("Skipping %s for now: %s", path.name, exc)
                    self._bad[path] = sig
                continue
            self._bad.pop(path, None)
            self._seen[path] = sig
            found.append(brief)
        return sorted(found, key=lambda b: b.generated_at)
