from pathlib import Path

from secretary.viewer_prompts import (
    dismiss_missing_file_prompt,
    missing_page_in,
    press_no_on_missing_file_prompt,
)


class FakeApi:
    def __init__(self, dialogs):
        self._dialogs = dialogs  # {hwnd: [(child, class, text)]}
        self.clicked: list[int] = []

    def dialogs(self):
        return [h for h in self._dialogs if h not in self.closed]

    @property
    def closed(self):
        return {h for h, kids in self._dialogs.items() if any(k[0] in self.clicked for k in kids)}

    def children(self, hwnd):
        return self._dialogs[hwnd]

    def click(self, hwnd):
        self.clicked.append(hwnd)


def prompt(path: Path, base: int) -> list:
    return [
        (base + 1, "Static", f'The file\n"{path}" doesn\'t exist anymore.\nKeep this file?'),
        (base + 2, "Button", "&Yes"),
        (base + 3, "Button", "&No"),
    ]


def test_presses_no_for_missing_pages_in_the_notes_folder(tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "Still here.md").write_text("x")
    api = FakeApi(
        {
            1: prompt(notes / "Still here.md", 10),  # exists: leave it
            2: prompt(tmp_path / "elsewhere" / "Gone.md", 20),  # not a notes page
            3: prompt(notes / "JuniperNotes_2026-11-10_2.md", 30),  # renamed: close
        }
    )
    assert press_no_on_missing_file_prompt(api, missing_page_in(notes))
    assert api.clicked == [33]


def test_answers_one_prompt_per_stale_tab(tmp_path):
    notes = tmp_path / "notes"
    api = FakeApi({1: prompt(notes / "A.md", 10), 2: prompt(notes / "B.md", 20)})
    answered = dismiss_missing_file_prompt(
        missing_page_in(notes), timeout=0.1, interval=0.01, api=api
    )
    assert answered == 2 and api.clicked == [13, 23]


def test_gives_up_when_no_prompt_shows(tmp_path):
    assert dismiss_missing_file_prompt(missing_page_in(tmp_path), timeout=0.01) == 0
