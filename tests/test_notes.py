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
        ("Open notes, call test test 2.", NoteRequest("new_page", "test test 2")),
        ("Start a new page call Escape room", NoteRequest("new_page", "Escape room")),
        ("Open the last notes page", NoteRequest("latest")),
        ("Can you open the one before that?", NoteRequest("previous")),
        ("the one before that", NoteRequest("previous")),
        ("Open the one before that, please.", NoteRequest("previous")),
        ("Can you go back a page?", NoteRequest("previous")),
        ("Go back one page.", NoteRequest("previous")),
        ("Right down, Dance Monkey.", NoteRequest("write", "Dance Monkey")),
        ("The code is 12, right that down.", NoteRequest("write", "The code is 12")),
        ("Right down the hall, then left?", None),
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
    kw.setdefault("default_page", "Notes")
    return Notebook(NotesConfig(dir=tmp_path / "notes", **kw), now=lambda: next(clock))


def test_notes_append_to_the_current_page(tmp_path):
    nb = notebook(tmp_path)  # the clock ticks a minute per read; naming the page takes one
    nb.write("the code is 4471")
    nb.write("blue key in the attic")
    page = tmp_path / "notes" / "Notes.md"
    assert page.read_text(encoding="utf-8") == (
        "# Notes\n\n- [17:01] the code is 4471\n- [17:02] blue key in the attic\n"
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


def test_numbers_match_as_digits_words_or_roman_numerals(tmp_path):
    nb = notebook(tmp_path)
    page = nb.new_page("Test Test II")
    assert nb.find_page("test test 2") == page
    assert nb.find_page("Test test two") == page
    assert nb.find_page("test test 3") is None


def test_she_starts_her_own_page_each_session(tmp_path):
    def session():
        return Notebook(
            NotesConfig(dir=tmp_path / "notes"), now=lambda: dt.datetime(2026, 11, 8, 15, 0)
        )

    first = session()
    assert first.write("a").name == "JuniperNotes_2026-11-08_1.md"
    assert first.write("b").name == "JuniperNotes_2026-11-08_1.md"
    second = session()  # closed and reopened
    assert second.write("c").name == "JuniperNotes_2026-11-08_2.md"


def test_a_page_opened_this_session_is_used(tmp_path):
    nb = notebook(tmp_path, default_page="JuniperNotes_{date}_{index}")
    nb.new_page("Escape room")
    assert nb.write("x").name == "Escape room.md"
    later = notebook(tmp_path, default_page="JuniperNotes_{date}_{index}")
    assert later.current_page().name == "Escape room.md"  # reading/opening: newest page
    assert later.write("y").name.startswith("JuniperNotes_")  # writing: her own page


def test_a_period_before_call_still_names_the_page():
    assert note_request("Open Notes. Call TestTest2.") == NoteRequest("new_page", "TestTest2")
    assert note_request("Open notes page, call, test for.") == NoteRequest("new_page", "test for")


def test_sound_alike_numbers_find_a_page(tmp_path):
    nb = notebook(tmp_path)
    four = nb.new_page("Test4")
    assert nb.find_page("Test For") == four
    assert nb.find_page("test to") is None
    sam = nb.new_page("Notes for Sam")
    assert nb.find_page("notes for sam") == sam


def test_split_then_write():
    from secretary.notes import split_then_write

    assert split_then_write("Open notes Test 4 and write down Jelly Beans") == (
        "Open notes Test 4",
        "write down Jelly Beans",
    )
    assert split_then_write("open Escape room, then write that down: 4471") == (
        "open Escape room",
        "write that down: 4471",
    )
    assert split_then_write("Write down, I need her to be able to read the gate code") is None
    assert split_then_write("open the door and walk in") is None


def test_recent_page_skips_old_pages(tmp_path):
    import os

    nb = notebook(tmp_path)
    assert nb.recent_page() is None
    page = nb.write("x")
    assert nb.recent_page() == page
    os.utime(page, (1, 1))
    assert notebook(tmp_path).recent_page() is None  # days old, new session


@pytest.mark.parametrize(
    ("text", "what"),
    [
        ("Mark all of the vault code notes as used.", "vault"),
        ("cross off the vault code", "vault"),
        ("archive the gate code", "gate"),
        ("cross the gate off", "gate"),
        ("Archive that.", ""),
        ("mark the password as done", "password"),
    ],
)
def test_marking_notes_used(text, what):
    assert note_request(text, "Juniper") == NoteRequest("used", what)


@pytest.mark.parametrize("text", ["mark my calendar", "check out this video"])
def test_not_marking_notes(text):
    assert note_request(text, "Juniper") is None


def test_mark_used_strikes_matching_notes(tmp_path):
    nb = notebook(tmp_path, timestamps=False)
    for note in ("VaultDigit2 is 7", "vault digit 4 is 2", "gate code 3258"):
        page = nb.write(note)
    assert nb.mark_used("vault") == 2
    assert page.read_text(encoding="utf-8") == (
        "# Notes\n\n- ~~VaultDigit2 is 7~~\n- ~~vault digit 4 is 2~~\n- gate code 3258\n"
    )
    assert nb.last_notes() == ["gate code 3258"]
    assert nb.mark_used("vault") == 0


def test_code_questions_skip_the_lookup():
    from secretary.notes import asks_for_a_code

    notes = ["VaultDigit2 is 7", "padlock 5 5 5 6"]
    assert asks_for_a_code("What is the vault code?", notes)
    assert asks_for_a_code("what's the padlock combination", notes)
    assert not asks_for_a_code("What is a padlock?", notes)
    assert not asks_for_a_code("what's the gate code", notes)


@pytest.mark.parametrize(
    ("text", "refers"),
    [
        ("Write down that vault code.", True),
        ("Write down the vault code from the notes.", True),
        ("Write down the vault code using the vault digits.", True),
        ("write down what you just said", True),
        ("write that down", False),
        ("write down the blue key is in the attic", False),
        ("write down that the vault code is 6762", False),
        ("write down gate code 2438", False),
    ],
)
def test_notes_that_point_at_something(text, refers):
    from secretary.notes import refers_to_something

    assert refers_to_something(text) is refers


def test_filled_note_is_tidied():
    from secretary.notes import filled_note

    assert filled_note('"Vault code 6762."\nThat is from your notes.') == "Vault code 6762"
    assert filled_note("Note: gate 3258") == "gate 3258"
    assert filled_note("UNKNOWN") == ""


def test_search_finds_notes_on_other_pages(tmp_path):
    nb = notebook(tmp_path, timestamps=False)
    nb.new_page("Test4")
    nb.write("gate code 3258")
    nb.write("padlock 5 5 5 6")
    current = nb.new_page("Today")
    nb.write("vault code 5526")
    assert nb.search("What was the gate code?", skip=current) == [("Test4", "gate code 3258")]
    assert nb.search("What was the code?", skip=current) == []


def test_rename_requests():
    assert note_request("Rename these notes, Haunted House.") == NoteRequest(
        "rename", "Haunted House"
    )
    assert note_request("call this page Lighthouse") == NoteRequest("rename", "Lighthouse")
    assert note_request("rename this page to Escape room") == NoteRequest("rename", "Escape room")


def test_rename_moves_the_file_and_heading(tmp_path):
    nb = notebook(tmp_path, timestamps=False)
    old = nb.write("vault code 1234")
    new = nb.rename("Haunted House")
    assert not old.exists() and new.name == "Haunted House.md"
    assert new.read_text(encoding="utf-8") == "# Haunted House\n\n- vault code 1234\n"
    assert nb.write("more").name == "Haunted House.md"
    nb.new_page("Other")
    with pytest.raises(FileExistsError):
        nb.rename("haunted house")
    assert nb.rename("Other Two").name == "Other Two.md"


def test_a_number_picks_one_note(tmp_path):
    nb = notebook(tmp_path, timestamps=False)
    page = nb.write("gate code 3258")
    nb.write("gate code 2438")
    nb.write("padlock 5 5 5 6")
    assert nb.mark_used("gate 2438") == 1
    assert nb.mark_used("padlock 5556") == 1
    assert page.read_text(encoding="utf-8").endswith(
        "- gate code 3258\n- ~~gate code 2438~~\n- ~~padlock 5 5 5 6~~\n"
    )


def test_an_asked_write_drops_the_question_mark():
    assert note_request("Hey, can you write down that the path is left, left?") == NoteRequest(
        "write", "the path is left, left"
    )
    assert note_request("write down: why?") == NoteRequest("write", "why?")


@pytest.mark.parametrize(
    "text",
    [
        "We'll rename this notes page to this one.",
        "Rename the current notes page to this one.",
        "let's rename the open page to this one",
    ],
)
def test_more_rename_wordings(text):
    assert note_request(text, "Juniper") == NoteRequest("rename", "this one")


def test_intent_openers():
    assert note_request("I'll go to the one before that.") == NoteRequest("previous")
    assert note_request("let's open the latest notes") == NoteRequest("latest")


def test_only_existing():
    from secretary.notes import only_existing

    assert only_existing("Go to the page we started with.")
    assert only_existing("Juniper, switch to the page called Test4")
    assert not only_existing("open a page called Notes 5")
    assert not only_existing("start a new page called Escape room")
