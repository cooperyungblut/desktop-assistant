"""Answer Notepad++'s "file doesn't exist anymore, keep it?" prompt for notes pages.

When she renames a page, Notepad++ still has the old file open and asks whether
to keep it in the editor, right away or the next time it gets focus (e.g. when
she opens another page). The app can't control Notepad++, but on Windows it can
find that message box (a standard dialog whose text names the file) and press
its No button, which closes the stale tab. Only a dialog naming a page in the
notes folder that no longer exists is touched, and only for a short while after
she renames or opens a page.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)


class WindowApi(Protocol):
    def dialogs(self) -> list[int]: ...
    def children(self, hwnd: int) -> list[tuple[int, str, str]]: ...  # (hwnd, class, text)
    def click(self, hwnd: int) -> None: ...


def missing_page_in(folder: Path) -> Callable[[str], bool]:
    """True for prompt text naming a .md file in `folder` that doesn't exist."""
    folder_key = os.path.normcase(os.path.abspath(folder))

    def matches(text: str) -> bool:
        for name in re.findall(r'"([^"]+?\.md)"', text):
            path = Path(name)
            if os.path.normcase(os.path.abspath(path.parent)) == folder_key and not path.exists():
                return True
        return False

    return matches


def press_no_on_missing_file_prompt(api: WindowApi, matches: Callable[[str], bool]) -> bool:
    """One pass: press No on a dialog whose text `matches`. True if it did."""
    for dialog in api.dialogs():
        children = api.children(dialog)
        if not any(matches(text) for _, cls, text in children if cls == "Static"):
            continue
        for hwnd, cls, text in children:
            if cls == "Button" and text.replace("&", "").strip().lower() == "no":
                api.click(hwnd)
                log.info("Closed a Notepad++ tab for a page that was renamed")
                return True
    return False


def dismiss_missing_file_prompt(
    matches: Callable[[str], bool],
    timeout: float = 20.0,
    interval: float = 0.25,
    api: WindowApi | None = None,
) -> int:
    """Watch for such prompts for up to `timeout` seconds; returns how many it
    answered (Notepad++ asks once per stale tab)."""
    api = api or _windows_api()
    if api is None:
        return 0
    answered = 0
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if press_no_on_missing_file_prompt(api, matches):
                answered += 1
                continue
        except OSError as exc:  # a window closed under us: try again next pass
            log.debug("dialog scan failed: %s", exc)
        time.sleep(interval)
    return answered


def dismiss_in_background(folder: Path, timeout: float = 20.0) -> None:
    if sys.platform == "win32":
        threading.Thread(
            target=dismiss_missing_file_prompt,
            args=(missing_page_in(folder), timeout),
            daemon=True,
        ).start()


def _windows_api() -> WindowApi | None:
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    WM_GETTEXT, BM_CLICK, SMTO_ABORTIFHUNG = 0x000D, 0x00F5, 0x0002

    def class_name(hwnd: int) -> str:
        buf = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, buf, 64)
        return buf.value

    def text(hwnd: int) -> str:
        # WM_GETTEXT works on another program's controls; GetWindowText doesn't.
        buf = ctypes.create_unicode_buffer(1024)
        result = wintypes.DWORD()
        user32.SendMessageTimeoutW(
            hwnd, WM_GETTEXT, 1024, buf, SMTO_ABORTIFHUNG, 500, ctypes.byref(result)
        )
        return buf.value

    class Api:
        def dialogs(self) -> list[int]:
            found: list[int] = []

            def visit(hwnd, _):
                if user32.IsWindowVisible(hwnd) and class_name(hwnd) == "#32770":
                    found.append(hwnd)
                return True

            user32.EnumWindows(enum_proc(visit), 0)
            return found

        def children(self, hwnd: int) -> list[tuple[int, str, str]]:
            found: list[tuple[int, str, str]] = []

            def visit(child, _):
                found.append((child, class_name(child), text(child)))
                return True

            user32.EnumChildWindows(hwnd, enum_proc(visit), 0)
            return found

        def click(self, hwnd: int) -> None:
            result = wintypes.DWORD()
            user32.SendMessageTimeoutW(
                hwnd, BM_CLICK, 0, 0, SMTO_ABORTIFHUNG, 1000, ctypes.byref(result)
            )

    return Api()
