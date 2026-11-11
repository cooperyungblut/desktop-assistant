"""Answer Notepad++'s "file doesn't exist anymore, keep it?" prompt after a rename.

When she renames a page, Notepad++ still has the old file open and asks whether
to keep it in the editor. The app can't control Notepad++, but on Windows it
can find that message box (a standard dialog whose text names the old file) and
press its No button, which closes the stale tab. Only a dialog that names the
renamed file is touched, and only for a short while after the rename.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)


class WindowApi(Protocol):
    def dialogs(self) -> list[int]: ...
    def children(self, hwnd: int) -> list[tuple[int, str, str]]: ...  # (hwnd, class, text)
    def click(self, hwnd: int) -> None: ...


def press_no_on_missing_file_prompt(api: WindowApi, path: Path) -> bool:
    """One pass: press No on a dialog that names `path`. True if it did."""
    for dialog in api.dialogs():
        children = api.children(dialog)
        if not any(path.name in text for _, cls, text in children if cls == "Static"):
            continue
        for hwnd, cls, text in children:
            if cls == "Button" and text.replace("&", "").strip().lower() == "no":
                api.click(hwnd)
                log.info("Closed Notepad++'s tab for the renamed %s", path.name)
                return True
    return False


def dismiss_missing_file_prompt(
    path: Path, timeout: float = 20.0, interval: float = 0.25, api: WindowApi | None = None
) -> bool:
    """Watch for the prompt about `path` for up to `timeout` seconds."""
    api = api or _windows_api()
    if api is None:
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if press_no_on_missing_file_prompt(api, path):
                return True
        except OSError as exc:  # a window closed under us: try again next pass
            log.debug("dialog scan failed: %s", exc)
        time.sleep(interval)
    return False


def dismiss_in_background(path: Path) -> None:
    if sys.platform == "win32":
        threading.Thread(target=dismiss_missing_file_prompt, args=(path,), daemon=True).start()


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
