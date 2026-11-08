import datetime as dt

import pytest

from secretary.config import NotesConfig
from secretary.notes import Notebook, NoteRequest, is_cancel, note_request, page_file_name


@pytest.mark.parametrize(
    ("text", "note"),
    [
        ("Write that down. The password is 4471.", "The password is 4471"),
        ("write down the password is 4471", "the password is 4471"),
        ("Juniper, write that down: 4471", "4471"),
        ("Take a note, cabinet code 3 5 9.", "cabinet code 3 5 9"),
        ("Make a note that the blue key is in the attic.", "the blue key is in the attic"),
        ("Note: red door first.", "red door first"),
        ("new note: lever left", "lever left"),
        ("Add that to my notes: lever three", "lever three"),
        ("hey, jot that down, 7 2 9", "7 2 9"),
        ("Write down where I put the key: under the mat", "where I put the key: under the mat"),
        ("The password is 4471, write that down.", "The password is 4471"),
        ("The password is 4471. Can you write that down for me?", "The password is 4471"),
        ("Can you write that down?", ""),
        ("Write this down.", ""),
        ("Okay so write that down", ""),
    ],
)
def test_write_requests(text, note):
    assert note_request(text, "Juniper") == NoteRequest("write", note)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Start a new page called Escape Room.", NoteRequest("new_page", "Escape Room")),
        ("new notes for the lighthouse puzzle", NoteRequest("new_page", "the lighthouse puzzle")),
        ("Start a new page.", NoteRequest("new_page", "")),
        ("Open a page called Notes 5.", NoteRequest("new_page", "Notes 5")),
        ("Open a page called Test5.", NoteRequest("new_page", "Test5")),
        ("Create a notes file named Lighthouse", NoteRequest("new_page", "Lighthouse")),
        ("Switch to the page called Escape room", NoteRequest("new_page", "Escape room")),
        ("go back to page Test2", NoteRequest("new_page", "Test2")),
        ("Open notes called Escape room.", NoteRequest("new_page", "Escape room")),
        ("open my notes called Test2", NoteRequest("new_page", "Test2")),
        (
            "Open the notes for the lighthouse puzzle",
            NoteRequest("new_page", "the lighthouse puzzle"),
        ),
        ("Open the latest notes.", NoteRequest("latest")),
        ("Open the last notes page", NoteRequest("latest")),
        ("Can you open the one before that?", NoteRequest("previous")),
        ("the one before that", NoteRequest("previous")),
        ("Open the one before that, please.", NoteRequest("previous")),
        ("Open my last notes page, please", NoteRequest("latest")),
        ("Read my notes please.", NoteRequest("read")),
        ("go back to the previous page", NoteRequest("previous")),
        ("Open the notes page test monitor on", NoteRequest("new_page", "test monitor on")),
        ("open the page Test2", NoteRequest("new_page", "Test2")),
        ("open my notes please", NoteRequest("open")),
        ("Open notes.", NoteRequest("open")),
        ("pull up my last page", NoteRequest("latest")),
        ("Juniper, show me the most recent notes", NoteRequest("latest")),
        ("Read me my notes.", NoteRequest("read")),
        ("What did I write down?", NoteRequest("read")),
        ("what's in my notes", NoteRequest("read")),
        ("Juniper, open my notes", NoteRequest("open")),
        ("Where did you write that down?", NoteRequest("where")),
        (
            "I'm asking this part for Claude, but where did you write that down for me?",
            NoteRequest("where"),
        ),
        ("Which page are my notes on?", NoteRequest("where")),
    ],
)
def test_page_read_and_open_requests(text, expected):
    assert note_request(text, "Juniper") == expected


@pytest.mark.parametrize(
    "text",
    [
        "what is the password",
        "I need to write a cover letter",
        "put it down",
        "read me the brief",
        "add lunch with Sam to my calendar tomorrow at noon",
        "what's the downside of that?",
        "Did you write that down?",
        "Why didn't you write that down",
    ],
)
def test_other_messages_are_not_notes(text):
    assert note_request(text, "Juniper") is None


def test_cancel():
    assert is_cancel("never mind") and is_cancel("Cancel.") and not is_cancel("4471")


def test_page_names_are_safe_on_windows():
    assert page_file_name("Escape Room") == "Escape Room.md"
    assert page_file_name('a/b:c*?"d.') == "a b c d.md"
    assert page_file_name("  ") == "Notes.md"


def notebook(tmp_path, **kw):
    clock = iter(dt.datetime(2026, 11, 5, 17, m) for m in range(60))
    return Notebook(NotesConfig(dir=tmp_path / "notes", **kw), now=lambda: next(clock))


def test_notes_append_to_the_current_page(tmp_path):
    nb = notebook(tmp_path)
    nb.write("the code is 4471")
    nb.write("blue key in the attic")
    page = tmp_path / "notes" / "Notes.md"
    assert page.read_text(encoding="utf-8") == (
        "# Notes\n\n- [17:00] the code is 4471\n- [17:01] blue key in the attic\n"
    )
    assert nb.last_notes() == ["the code is 4471", "blue key in the attic"]


def test_new_page_becomes_current_and_survives_restart(tmp_path):
    nb = notebook(tmp_path, timestamps=False)
    nb.write("first")
    nb.new_page("Escape Room")
    nb.write("lever three")
    room = tmp_path / "notes" / "Escape Room.md"
    assert room.read_text(encoding="utf-8") == "# Escape Room\n\n- lever three\n"
    # A restart picks up the page written last.
    assert notebook(tmp_path).current_page() == room


def test_unnamed_page_gets_a_dated_name(tmp_path):
    assert notebook(tmp_path).new_page("").name == "Notes 2026-11-05 1700.md"


def test_open_without_a_viewer_does_nothing(tmp_path):
    assert notebook(tmp_path).open() is False


def test_windows_launch_goes_through_start(monkeypatch, tmp_path):
    from secretary import notes

    monkeypatch.setattr(notes.sys, "platform", "win32")
    page = tmp_path / "Test.md"
    exe = "C:/Program Files/Notepad++/notepad++.exe"
    assert notes._launch_command(exe, page) == f'cmd /c start "" "{exe}" "{page}"'
    assert notes._launch_command("default", page) == f'cmd /c start "" "{page}"'
    assert (
        notes._launch_command(exe, page, ["-monitor"])
        == f'cmd /c start "" "{exe}" "-monitor" "{page}"'
    )


def test_notepad_plus_plus_gets_monitor_by_default():
    from secretary.notes import viewer_args

    assert viewer_args("C:/Program Files (x86)/Notepad++/notepad++.exe", None) == ["-monitor"]
    assert viewer_args("C:\\Tools\\code.exe", None) == []
    assert viewer_args("C:/Notepad++/notepad++.exe", []) == []


def test_open_with_a_missing_program_says_so(tmp_path):
    nb = notebook(tmp_path, open_with=str(tmp_path / "nope" / "notepad++.exe"))
    assert nb.open() is False


def test_open_starts_the_viewer(monkeypatch, tmp_path):
    from secretary import notes

    started = []
    monkeypatch.setattr(notes.subprocess, "Popen", lambda cmd, **kw: started.append(cmd))
    exe = tmp_path / "viewer.exe"
    exe.write_text("")
    nb = notebook(tmp_path, open_with=str(exe))
    page = nb.new_page("Test")
    assert nb.open(page) is True
    assert started and str(page) in str(started[0])


def test_latest_page_is_the_newest_file(tmp_path):
    import os

    nb = notebook(tmp_path)
    old = nb.new_page("Old")
    nb.write("x")
    new = nb.new_page("New")
    nb.write("y")
    os.utime(old, (1, 1))
    nb.new_page("Old")  # current is Old, but New was written last
    assert nb.latest_page() == new


def test_spoken_names_find_existing_pages(tmp_path):
    nb = notebook(tmp_path)
    page = nb.new_page("testMonitorOn")
    nb.new_page("Other")
    assert nb.find_page("test monitor on") == page
    assert nb.new_page("Test Monitor On.") == page
    assert nb.current_page() == page
    assert nb.find_page("nope") is None


def test_previous_steps_back_through_pages(tmp_path):
    import os

    nb = notebook(tmp_path)
    for i, name in enumerate(("A", "B", "C")):
        page = nb.new_page(name)
        nb.write(name)
        os.utime(page, (i + 1, i + 1))
    assert nb.latest_page().stem == "C"
    assert nb.previous_page().stem == "B"
    assert nb.previous_page().stem == "A"
    assert nb.previous_page() is None
    assert nb.current_page().stem == "A"


def test_bare_open_names_a_page():
    from secretary.notes import open_page_name

    assert open_page_name("Open Test Monitor on.") == "Test Monitor on"
    assert open_page_name("Juniper, switch to Test2 please", "Juniper") == "Test2"
    assert open_page_name("open the Escape room page") == "Escape room"
    assert open_page_name("Open, test monitor on.") == "test monitor on"
    assert open_page_name("Open test monitor on, please.") == "test monitor on"
    assert note_request("Open notes called Test Monitor On, please.") == NoteRequest(
        "new_page", "Test Monitor On"
    )
    assert open_page_name("what time is it") is None


def test_please_stays_in_a_note():
    assert note_request("write down: ask her please") == NoteRequest("write", "ask her please")
