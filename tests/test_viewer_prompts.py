from pathlib import Path

from secretary.viewer_prompts import dismiss_missing_file_prompt, press_no_on_missing_file_prompt

OLD = Path("C:/dev/notes/JuniperNotes_2026-11-10_2.md")


class FakeApi:
    def __init__(self, dialogs):
        self._dialogs = dialogs  # {hwnd: [(child, class, text)]}
        self.clicked: list[int] = []

    def dialogs(self):
        return list(self._dialogs)

    def children(self, hwnd):
        return self._dialogs[hwnd]

    def click(self, hwnd):
        self.clicked.append(hwnd)


def prompt(name: str) -> list:
    return [
        (11, "Static", f'The file "C:\\dev\\notes\\{name}" doesn\'t exist anymore.'),
        (12, "Button", "&Yes"),
        (13, "Button", "&No"),
    ]


def test_presses_no_on_the_prompt_for_the_renamed_file():
    api = FakeApi({1: prompt("Other.md"), 2: prompt(OLD.name)})
    assert press_no_on_missing_file_prompt(api, OLD)
    assert api.clicked == [13]


def test_leaves_other_dialogs_alone():
    api = FakeApi({1: prompt("Other.md")})
    assert not dismiss_missing_file_prompt(OLD, timeout=0.05, interval=0.01, api=api)
    assert api.clicked == []


def test_gives_up_when_no_prompt_shows():
    assert dismiss_missing_file_prompt(OLD, timeout=0.01) is False
