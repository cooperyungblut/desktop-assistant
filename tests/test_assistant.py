import asyncio
import shutil

import pytest

from secretary import escalations
from secretary.assistant import Assistant
from secretary.state import StateStore
from secretary.tts import SilentSpeaker
from secretary.watcher import BriefScanner
from tests.conftest import SAMPLES


@pytest.fixture
def env(tmp_path, persona, fake_model):
    briefs = tmp_path / "Secretary" / "briefs"
    escs = tmp_path / "Secretary" / "escalations"
    briefs.mkdir(parents=True)
    escs.mkdir()
    events = []

    async def emit(e):
        events.append(e)

    a = Assistant(
        persona=persona,
        model=fake_model,
        speaker=SilentSpeaker(),
        scanner=BriefScanner(briefs),
        state=StateStore(tmp_path / "state.json"),
        escalations_dir=escs,
        emit=emit,
    )
    return a, briefs, escs, events


async def finish(a: Assistant):
    await asyncio.wait_for(a._task, 5)


async def test_new_brief_shows_ready_then_handled(env):
    a, briefs, _, events = env
    await a.poll()
    assert events == []  # empty folder: nothing to report

    shutil.copy(SAMPLES / "2026-11-02_morning.md", briefs)
    await a.poll()
    assert events[-1]["type"] == "briefs" and events[-1]["ready"] == "2026-11-02_morning"

    a.start_brief()
    await finish(a)
    types = [e["type"] for e in events]
    assert "readout" in types and types.count("say") >= 5
    assert [e["type"] for e in events[-2:]] == ["say", "speech_done"]
    assert a.ready_brief() is None


async def test_half_synced_file_is_retried(env):
    a, briefs, _, events = env
    full = (SAMPLES / "2026-11-02_morning.md").read_text(encoding="utf-8")
    target = briefs / "2026-11-02_morning.md"
    target.write_text(full.split("# Spoken")[0], encoding="utf-8")
    await a.poll()
    assert a.ready_brief() is None

    target.write_text(full, encoding="utf-8")
    await a.poll()
    assert a.ready_brief().id == "2026-11-02_morning"


async def test_follow_up_uses_brief_and_history(env, fake_model):
    a, briefs, _, events = env
    shutil.copy(SAMPLES / "2026-11-02_morning.md", briefs)
    await a.poll()
    a.start_brief()
    await finish(a)

    a.ask("when is the coffee chat?")
    await finish(a)
    sent = fake_model.requests[-1]
    assert "<brief>" in sent[0]["content"]
    assert sent[1]["role"] == "assistant" and sent[1]["content"].startswith("Heads up")
    assert [e["type"] for e in events[-2:]] == ["say", "speech_done"]
    assert a.history[-1].content == "It's at ten. Have fun!"


async def test_questions_use_newest_brief_before_any_readout(env, fake_model):
    # After a restart nothing has been read out in this session, but the brief
    # was already read earlier: questions should still be about it.
    a, briefs, _, _ = env
    shutil.copy(SAMPLES / "2026-11-02_morning.md", briefs)
    await a.poll()
    a.ask("when is the coffee chat?")
    await finish(a)
    assert "<brief>" in fake_model.requests[-1][0]["content"]
    assert a.current is None  # nothing was read out or marked handled
    assert a.ready_brief().id == "2026-11-02_morning"


async def test_asking_for_a_brief_reads_it_verbatim(env, fake_model):
    a, briefs, _, events = env
    shutil.copy(SAMPLES / "2026-11-02_morning.md", briefs)
    await a.poll()
    a.ask("Can you read the sample brief?")
    await finish(a)
    assert fake_model.requests == []  # never through the model
    assert any(e["type"] == "readout" for e in events)
    assert a.current.id == "2026-11-02_morning"


async def test_asking_for_a_missing_brief_says_so(env, fake_model):
    a, briefs, _, events = env
    shutil.copy(SAMPLES / "2026-11-02_morning.md", briefs)
    await a.poll()
    a.ask("read yesterday's evening brief")
    await finish(a)
    assert fake_model.requests == []
    said = [e["text"] for e in events if e["type"] == "say"]
    assert said == ["I don't have an evening brief from yesterday."]


async def test_escalation_writes_file_without_calling_model(env, fake_model):
    a, briefs, escs, events = env
    shutil.copy(SAMPLES / "2026-11-02_morning.md", briefs)
    await a.poll()
    a.start_brief()
    await finish(a)

    a.ask("/claude is the Contoso screen technical?")
    await finish(a)
    assert fake_model.requests == []
    files = list(escs.glob("esc-*.md"))
    assert len(files) == 1
    e = escalations.parse(files[0].read_text(encoding="utf-8"))
    assert e.question == "is the Contoso screen technical?"
    assert e.about_brief == "2026-11-02_morning"
    assert any(ev["type"] == "escalated" for ev in events)


async def test_brief_marks_its_escalations_answered(env):
    a, briefs, escs, _ = env
    e = escalations.new_escalation("Technical?", about_brief=None)
    e.id = "esc-20261102-101503"  # the id the sample afternoon brief answers
    escalations.write(e, escs)

    shutil.copy(SAMPLES / "2026-11-02_afternoon.md", briefs)
    await a.poll()
    back = escalations.parse((escs / f"{e.id}.md").read_text(encoding="utf-8"))
    assert back.status is escalations.Status.ANSWERED
    assert back.answered_in == "2026-11-02_afternoon"


async def test_newest_unhandled_brief_is_offered(env):
    a, briefs, _, _ = env
    for t in ("morning", "afternoon"):
        shutil.copy(SAMPLES / f"2026-11-02_{t}.md", briefs)
    await a.poll()
    assert a.ready_brief().id == "2026-11-02_afternoon"


async def test_interrupted_reply_is_closed_out(env, persona):
    a, _, _, events = env

    class SlowModel:
        async def stream_chat(self, messages, options):
            yield "Let me check"
            await asyncio.sleep(10)
            yield " never"

    a._model = SlowModel()
    a.ask("first question")
    await asyncio.sleep(0.05)
    a.ask("second question")  # interrupts the first
    await asyncio.sleep(0.05)
    ends = [e["text"] for e in events if e["type"] == "reply_end"]
    assert ends == ["Let me check (stopped)"]
    a.stop()


class FakeTranscriber:
    available = True

    def __init__(self, text):
        self.text = text
        self.hints = []

    async def load(self):
        pass

    async def transcribe(self, audio, hints=None):
        self.hints.append(hints)
        return self.text


async def test_voice_question_is_transcribed_then_answered(env, fake_model):
    a, briefs, _, events = env
    a._transcriber = FakeTranscriber("When is my coffee chat?")
    a.voice(b"audio")
    await finish(a)
    types = [e["type"] for e in events]
    assert types.index("transcript") < types.index("reply_start")
    assert events[types.index("transcript")]["text"] == "When is my coffee chat?"
    assert fake_model.requests[-1][-1]["content"] == "When is my coffee chat?"


async def test_voice_can_escalate(env, fake_model):
    a, _, escs, _ = env
    a._transcriber = FakeTranscriber("Check with Claude whether Dana replied.")
    a.voice(b"audio")
    await finish(a)
    assert fake_model.requests == []
    assert len(list(escs.glob("esc-*.md"))) == 1


async def test_nothing_heard_does_not_call_the_model(env, fake_model):
    a, _, _, events = env
    a._transcriber = FakeTranscriber("")
    a.voice(b"audio")
    await finish(a)
    assert fake_model.requests == []
    assert events == [{"type": "transcript", "text": ""}]


async def test_voice_without_stt_reports_an_error(env, fake_model):
    a, _, _, events = env  # default transcriber: speech input off
    a.voice(b"audio")
    await finish(a)
    assert [e["type"] for e in events] == ["transcript", "error"]


async def test_voice_hints_include_names_from_the_current_brief(env):
    a, briefs, _, _ = env
    shutil.copy(SAMPLES / "2026-11-02_morning.md", briefs)
    await a.poll()
    a.start_brief()
    await finish(a)

    a._transcriber = t = FakeTranscriber("What did Northwind say?")
    a.voice(b"audio")
    await finish(a)
    assert "Northwind Traders" in t.hints[-1] and "Juniper" in t.hints[-1]


async def test_mood_tag_drives_expression_and_is_never_shown(env, fake_model):
    a, _, _, events = env
    fake_model.reply = "[sad] Sorry, Contoso passed. The other two are still open."
    a.ask("any news?")
    await finish(a)

    exprs = [e for e in events if e["type"] == "expression"]
    assert exprs[0]["mood"] == "sad" and exprs[0]["faces"] == {"sad": 0.45}
    shown = "".join(e["text"] for e in events if e["type"] == "reply_token")
    spoken = " ".join(e["text"] for e in events if e["type"] == "say")
    assert "[" not in shown and "[" not in spoken
    assert spoken.startswith("Sorry, Contoso passed.")
    # History keeps the tag so the model keeps seeing the format.
    assert a.history[-1].content.startswith("[sad] Sorry")


async def test_reply_without_tag_falls_back_to_neutral(env):
    a, _, _, events = env
    a.ask("when is it?")
    await finish(a)
    exprs = [e for e in events if e["type"] == "expression"]
    assert [e["mood"] for e in exprs] == ["neutral"]
    assert exprs[0]["faces"] == {} and exprs[0]["talk_faces"] == {}


# ---- calendar ------------------------------------------------------------------


class FakeCalendar:
    available = True

    def __init__(self, error: str | None = None):
        self.added = []
        self.found = []
        self.agenda_events = []
        self.removed = None
        self.error = error

    async def add(self, event, *, added_by=None):
        from secretary.google_calendar import CalendarError

        if self.error:
            raise CalendarError(self.error)
        self.added.append(event)
        self.added_by = added_by
        return "https://calendar/e1"

    async def find(self, request, now):
        self.searched = request
        return self.found

    async def agenda(self, request):
        self.asked = request
        if self.error:
            from secretary.google_calendar import CalendarError

            raise CalendarError(self.error)
        return self.agenda_events

    async def remove(self, found):
        if self.error:
            from secretary.google_calendar import CalendarError

            raise CalendarError(self.error)
        self.removed = found

    async def aclose(self):
        pass


def calendar_env(env, calendar):
    import datetime as dt

    a, *_, events = env
    a._calendar = calendar
    a._now = lambda: dt.datetime(2026, 11, 4, 15, 0)
    return a, events


def replies(events):
    return [e["text"] for e in events if e["type"] == "reply_end"]


async def test_calendar_event_is_added_only_after_yes(env, fake_model):
    cal = FakeCalendar()
    a, events = calendar_env(env, cal)
    a.ask("Add lunch with Sam to my calendar tomorrow at noon")
    await finish(a)
    assert replies(events) == [
        "Lunch with Sam, Thursday, November 5, noon to 1 PM. Should I add it to your calendar?"
    ]
    assert cal.added == [] and fake_model.requests == []  # never via the model

    a.ask("yes")
    await finish(a)
    assert [e.title for e in cal.added] == ["Lunch with Sam"]
    assert cal.added_by == "Juniper"
    assert replies(events)[-1] == "Done, it's on your calendar."
    assert {
        "type": "calendar_added",
        "title": "Lunch with Sam",
        "link": "https://calendar/e1",
    } in events

    a.ask("yes")  # nothing pending any more: goes to the model as usual
    await finish(a)
    assert len(cal.added) == 1 and len(fake_model.requests) == 1


async def test_calendar_no_or_something_else_drops_it(env, fake_model):
    cal = FakeCalendar()
    a, events = calendar_env(env, cal)
    a.ask("add the dentist to my calendar friday at 3")
    await finish(a)
    a.ask("no")
    await finish(a)
    assert replies(events)[-1] == "Okay, I won't add it."

    a.ask("add the dentist to my calendar friday at 3")
    await finish(a)
    a.ask("when is the coffee chat?")  # not an answer: handled normally, event dropped
    await finish(a)
    a.ask("yes")
    await finish(a)
    assert cal.added == [] and len(fake_model.requests) == 2


async def test_calendar_problems_and_failures_are_spoken(env):
    a, events = calendar_env(env, FakeCalendar(error="Google Calendar didn't answer"))
    a.ask("add lunch to my calendar")
    await finish(a)
    assert replies(events)[-1] == "When is it?"

    a.ask("add lunch to my calendar friday at noon")
    await finish(a)
    a.ask("yes")
    await finish(a)
    assert replies(events)[-1] == "I couldn't add that: Google Calendar didn't answer."
    assert events[-1]["type"] == "speech_done"


async def test_calendar_not_connected(env, fake_model):
    a, _, _, events = env
    a.ask("add lunch to my calendar friday at noon")
    await finish(a)
    assert replies(events) == [a.persona.lines.calendar_off]
    assert fake_model.requests == []


def found_event(title="Test event"):
    import datetime as dt

    from secretary.calendar_requests import EventDraft
    from secretary.google_calendar import FoundEvent

    start = dt.datetime(2026, 11, 5, 15, 0)
    return FoundEvent("a1", EventDraft(title, start.date(), start, start.replace(hour=16)))


async def test_calendar_removal_asks_then_deletes_after_yes(env, fake_model):
    cal = FakeCalendar()
    cal.found = [found_event()]
    a, events = calendar_env(env, cal)
    a.ask("Remove the test event from my calendar")
    await finish(a)
    assert replies(events) == [
        "Test event, Thursday, November 5, 3 to 4 PM. Should I remove it from your calendar?"
    ]
    assert cal.searched.title == "test" and cal.removed is None

    a.ask("yes")
    await finish(a)
    assert cal.removed == cal.found[0]
    assert replies(events)[-1] == "Done, I took it off your calendar."
    assert {"type": "calendar_removed", "title": "Test event"} in events
    assert fake_model.requests == []


async def test_calendar_removal_no_keeps_the_event(env):
    cal = FakeCalendar()
    cal.found = [found_event()]
    a, events = calendar_env(env, cal)
    a.ask("remove the test event from my calendar")
    await finish(a)
    a.ask("no")
    await finish(a)
    assert cal.removed is None and replies(events)[-1] == "Okay, I won't add it."


async def test_calendar_removal_nothing_found_or_too_many(env):
    cal = FakeCalendar()
    a, events = calendar_env(env, cal)
    a.ask("remove the dentist from my calendar")
    await finish(a)
    assert replies(events)[-1] == "I don't see an event like that that I added."

    cal.found = [found_event("Test one"), found_event("Test two")]
    a.ask("remove the test from my calendar")
    await finish(a)
    assert replies(events)[-1].startswith("I found 2 like that: Test one, Thursday")
    a.ask("yes")  # nothing pending, so nothing is removed
    await finish(a)
    assert cal.removed is None


async def test_calendar_removal_failure_is_spoken(env):
    cal = FakeCalendar(error="Google Calendar didn't answer")
    cal.found = [found_event()]
    a, events = calendar_env(env, cal)
    cal.error = None
    a.ask("remove the test event from my calendar")
    await finish(a)
    cal.error = "Google Calendar didn't answer"
    a.ask("yes")
    await finish(a)
    assert replies(events)[-1] == "I couldn't remove that: Google Calendar didn't answer."


async def test_calendar_question_is_answered_from_the_calendar(env, fake_model):
    cal = FakeCalendar()
    cal.agenda_events = [found_event("Coffee chat").draft, found_event("Mock interview").draft]
    a, events = calendar_env(env, cal)
    a.ask("What's on my calendar tomorrow?")
    await finish(a)
    assert replies(events) == [
        "You have 2 things tomorrow: Coffee chat at 3 PM; Mock interview at 3 PM."
    ]
    assert fake_model.requests == []  # built in code, not summarized by the model


async def test_calendar_question_clear_day_and_failure(env):
    cal = FakeCalendar()
    a, events = calendar_env(env, cal)
    a.ask("what's on my calendar on friday")
    await finish(a)
    assert replies(events)[-1] == "Your calendar is clear on Friday, November 6."

    cal.error = "Google Calendar didn't answer"
    a.ask("read me my calendar")
    await finish(a)
    assert replies(events)[-1] == "I couldn't read your calendar: Google Calendar didn't answer."


async def test_calendar_question_when_not_connected(env, fake_model):
    a, _, _, events = env
    a.ask("what's on my calendar today")
    await finish(a)
    assert replies(events) == [a.persona.lines.calendar_off]


class FakeWiki:
    def __init__(self, passages=None, fail=False):
        from secretary.wiki import Passage

        self.passages = passages or [
            Passage(
                "Nicolas Cage", "Nicolas Cage (born January 7, 1964) is an actor.", 100, "title"
            )
        ]
        self.fail = fail
        self.questions: list[str] = []
        self.loose: list[bool] = []
        self.interrupted = False

    def interrupt(self):
        self.interrupted = True

    def search(self, question, limit=3, *, loose=True):
        self.questions.append(question)
        self.loose.append(loose)
        if self.fail:
            raise RuntimeError("index broke")
        return self.passages


def _with_wiki(env, wiki):
    a, *_ = env
    a._wiki = wiki
    return a


async def test_factual_question_gets_wikipedia_passages_in_the_prompt(env, fake_model):
    wiki = FakeWiki()
    a = _with_wiki(env, wiki)
    a.ask("What is Nicolas Cage's birthday?")
    await finish(a)
    messages = fake_model.requests[-1]
    wiki_message = next(m for m in messages if "<wikipedia>" in m["content"])
    assert "[Nicolas Cage] Nicolas Cage (born January 7, 1964)" in wiki_message["content"]
    # right before the persona reminder and the question
    assert messages.index(wiki_message) == len(messages) - 3
    assert wiki.questions == ["What is Nicolas Cage's birthday?"]


async def test_wikipedia_text_is_not_kept_in_history(env, fake_model):
    a = _with_wiki(env, FakeWiki())
    a.ask("Who is Nicolas Cage?")
    await finish(a)
    assert all("Wikipedia" not in t.content and "1964" not in t.content for t in a.history)


async def test_personal_questions_skip_the_lookup(env, fake_model):
    wiki = FakeWiki()
    a = _with_wiki(env, wiki)
    a.ask("What time is my interview?")
    await finish(a)
    assert wiki.questions == []
    assert not any("<wikipedia>" in m["content"] for m in fake_model.requests[-1])


async def test_a_failing_index_falls_back_to_a_normal_answer(env, fake_model):
    a = _with_wiki(env, FakeWiki(fail=True))
    a.ask("Who is Nicolas Cage?")
    await finish(a)
    assert fake_model.requests
    assert not any("<wikipedia>" in m["content"] for m in fake_model.requests[-1])


async def test_nothing_usable_means_no_wikipedia_block(env, fake_model):
    from secretary.wiki import Passage

    a = _with_wiki(env, FakeWiki([Passage("Random", "Unrelated.", 1, "any")]))
    a.ask("Who is Nicolas Cage?")
    await finish(a)
    assert not any("<wikipedia>" in m["content"] for m in fake_model.requests[-1])
    # ...but she is told it was looked up and nothing was found, so she hedges.
    assert any("nothing about it was found" in m["content"] for m in fake_model.requests[-1])


async def test_a_question_not_looked_up_gets_no_nothing_found_note(env, fake_model):
    a = _with_wiki(env, FakeWiki())
    a.ask("How are you today?")
    await finish(a)
    assert not any("nothing about it was found" in m["content"] for m in fake_model.requests[-1])


async def test_a_slow_lookup_is_skipped_and_she_still_answers(env, fake_model):
    import time

    class SlowWiki(FakeWiki):
        def search(self, question, limit=3, *, loose=True):
            time.sleep(0.4)
            return super().search(question, limit)

    wiki = SlowWiki()
    a = _with_wiki(env, wiki)
    a._wiki_timeout = 0.05
    a.ask("Who is Nicolas Cage?")
    await finish(a)
    assert fake_model.requests
    assert wiki.interrupted  # the abandoned search is stopped
    assert not any("<wikipedia>" in m["content"] for m in fake_model.requests[-1])


class _SlowWiki(FakeWiki):
    def __init__(self, delay):
        super().__init__()
        self.delay = delay

    def search(self, question, limit=3, *, loose=True):
        import time

        time.sleep(self.delay)
        return super().search(question, limit)


async def test_a_slowish_lookup_says_one_moment_then_answers_from_it(env, fake_model):
    a = _with_wiki(env, _SlowWiki(0.3))
    events = env[3]
    a._wiki_notice_after, a._wiki_timeout = 0.05, 5.0
    a.ask("Who is Nicolas Cage?")
    await finish(a)
    said = [e["text"] for e in events if e["type"] == "say"]
    assert said[0] in a.persona.lines.looking_up
    assert any("<wikipedia>" in m["content"] for m in fake_model.requests[-1])
    assert all(t.content not in a.persona.lines.looking_up for t in a.history)


async def test_a_quick_lookup_says_nothing_extra(env, fake_model):
    a = _with_wiki(env, FakeWiki())
    events = env[3]
    a.ask("Who is Nicolas Cage?")
    await finish(a)
    assert not any(e.get("text") in a.persona.lines.looking_up for e in events)


def test_one_moment_lines_vary_and_never_repeat_back_to_back(env):
    a, *_ = env
    picks = [a._looking_up_line() for _ in range(40)]
    assert all(x != y for x, y in zip(picks, picks[1:], strict=False))
    assert len(set(picks)) > 2


def test_a_single_one_moment_line_still_works(env):
    a, *_ = env
    a.persona.lines.looking_up = ["Hold on."]
    assert [a._looking_up_line() for _ in range(3)] == ["Hold on."] * 3


async def test_a_follow_up_question_is_looked_up_with_the_last_topic(env, fake_model):
    from secretary.wiki import Passage

    wiki = FakeWiki([Passage("StarCraft", "StarCraft is a franchise.", 100, "title")])
    a = _with_wiki(env, wiki)
    a.ask("What is StarCraft?")
    await finish(a)
    a.ask("What are those four species?")
    await finish(a)
    assert wiki.questions == [
        "What is StarCraft?",
        "What are those four species? (StarCraft)",
    ]


async def test_any_word_matches_only_for_an_explicit_look_up(env, fake_model):
    wiki = FakeWiki()
    a = _with_wiki(env, wiki)
    a.ask("Who is Nicolas Cage?")
    await finish(a)
    a.ask("Look up Nicolas Cage")
    await finish(a)
    assert wiki.loose == [False, True]


# ---- notes ---------------------------------------------------------------------


def notes_env(env, tmp_path):
    from secretary.config import NotesConfig
    from secretary.notes import Notebook

    a, _, _, events = env
    config = NotesConfig(dir=tmp_path / "notes", timestamps=False, default_page="Notes")
    a._notebook = Notebook(config)
    return a, events, tmp_path / "notes" / "Notes.md"


async def test_write_that_down_appends_a_note(env, tmp_path, fake_model):
    a, events, page = notes_env(env, tmp_path)
    a.ask("The password is 4471, write that down.")
    await finish(a)
    assert page.read_text(encoding="utf-8").endswith("- The password is 4471\n")
    assert {"type": "note_added", "page": "Notes", "text": "The password is 4471"} in events
    assert a.history[-1].content == "Got it, I wrote down: The password is 4471."
    assert fake_model.requests == []  # never goes through the model


async def test_write_that_down_alone_asks_what_to_write(env, tmp_path, fake_model):
    a, events, page = notes_env(env, tmp_path)
    a.ask("Can you write that down?")
    await finish(a)
    assert a.history[-1].content == "Sure, what should I write down?"
    a.ask("Lever three, then lever one.")
    await finish(a)
    assert page.read_text(encoding="utf-8").endswith("- Lever three, then lever one\n")

    a.ask("write that down")
    await finish(a)
    a.ask("never mind")
    await finish(a)
    assert a.history[-1].content == "Okay, I won't write anything."
    assert fake_model.requests == []


async def test_read_my_notes(env, tmp_path):
    a, _, _ = notes_env(env, tmp_path)
    for note in ("note one", "note two"):
        a.ask(f"write down {note}")
        await finish(a)
    a.ask("read me my notes")
    await finish(a)
    assert a.history[-1].content == "Your last notes on Notes: note one; note two."


async def test_notes_off_without_a_notebook(env):
    a, _, _, _ = env
    a.ask("write that down: 4471")
    await finish(a)
    assert a.history[-1].content == "Note taking is turned off in my settings."


async def test_new_page_tells_you_when_the_viewer_wont_open(env, tmp_path):
    from secretary.config import NotesConfig
    from secretary.notes import Notebook

    a, _, _, _ = env
    missing = str(tmp_path / "nope.exe")
    a._notebook = Notebook(NotesConfig(dir=tmp_path / "notes", open_with=missing))
    a.ask("start a new page called Test")
    await finish(a)
    assert (tmp_path / "notes" / "Test.md").is_file()
    assert a.history[-1].content == (
        f"New page started: Test. I couldn't open it with {missing}. Check notes open_with."
    )


async def test_going_back_to_a_page(env, tmp_path):
    a, _, _ = notes_env(env, tmp_path)
    a.ask("open a page called Test5")
    await finish(a)
    assert a.history[-1].content == "New page started: Test5."
    a.ask("start a new page called Other")
    await finish(a)
    a.ask("switch to the page called Test5")
    await finish(a)
    assert a.history[-1].content == "Back on Test5."


async def test_open_the_latest_notes(env, tmp_path):
    a, _, _ = notes_env(env, tmp_path)
    a.ask("open a page called Test5")
    await finish(a)
    a.ask("write that down: 42")
    await finish(a)
    a.ask("open the latest notes")
    await finish(a)
    # No viewer set in the test config, so she says where they are instead.
    assert a.history[-1].content.startswith("Your notes are in")


async def test_reopening_a_page_by_its_spoken_name(env, tmp_path):
    a, _, _ = notes_env(env, tmp_path)
    for text in ("open a page called testMonitorOn", "start a new page called Test Test II"):
        a.ask(text)
        await finish(a)
    a.ask("Open the notes page test monitor on")
    await finish(a)
    assert a.history[-1].content == "Back on testMonitorOn."
    a.ask("Write this down, 42,313.")
    await finish(a)
    page = tmp_path / "notes" / "testMonitorOn.md"
    assert page.read_text(encoding="utf-8").endswith("- 42,313\n")


async def test_the_page_before_that(env, tmp_path):
    a, _, _ = notes_env(env, tmp_path)
    a.ask("open a page called Only")
    await finish(a)
    a.ask("write that down: 1")
    await finish(a)
    a.ask("open the latest notes")
    await finish(a)
    a.ask("Can you open the one before that?")
    await finish(a)
    assert a.history[-1].content == "That's the oldest page I have."


async def test_bare_open_switches_only_to_existing_pages(env, tmp_path, fake_model):
    a, _, _ = notes_env(env, tmp_path)
    for text in ("open a page called testMonitorOn", "start a new page called Other"):
        a.ask(text)
        await finish(a)
    a.ask("Open Test Monitor on.")
    await finish(a)
    assert a.history[-1].content == "Back on testMonitorOn."
    a.ask("open the door")  # not a page: goes to the model
    await finish(a)
    assert fake_model.requests


async def test_switch_to_a_page_said_with_digits(env, tmp_path):
    a, _, _ = notes_env(env, tmp_path)
    for text in ("open a page called Test Test II", "start a new page called Other"):
        a.ask(text)
        await finish(a)
    a.ask("Switch to test test 2.")
    await finish(a)
    assert a.history[-1].content == "Back on Test Test II."


async def test_open_a_page_and_write_in_one_go(env, tmp_path):
    a, _, _ = notes_env(env, tmp_path)
    a.ask("open a page called Test4")
    await finish(a)
    a.ask("start a new page called Other")
    await finish(a)
    a.ask("Open notes called Test 4 and write down jelly beans")
    await finish(a)
    page = tmp_path / "notes" / "Test4.md"
    assert page.read_text(encoding="utf-8").endswith("- jelly beans\n")
    assert not list((tmp_path / "notes").glob("*jelly*"))
    assert a.history[-1].content == "Got it, I wrote down: jelly beans."


async def test_she_sees_the_page_in_use_when_answering(env, tmp_path, fake_model):
    a, _, _ = notes_env(env, tmp_path)
    a.ask("write down gate code 2438")
    await finish(a)
    a.ask("Check the notes for the gate code.")
    await finish(a)
    sent = str(fake_model.requests[-1])
    assert "gate code 2438" in sent and '"Notes"' in sent


async def test_crossing_off_notes(env, tmp_path, fake_model):
    a, _, page = notes_env(env, tmp_path)
    for note in ("vault digit 1 is 6", "vault digit 2 is 7", "gate code 3258"):
        a.ask(f"write down {note}")
        await finish(a)
    a.ask("Mark all of the vault code notes as used.")
    await finish(a)
    assert a.history[-1].content == "Crossed off 2 notes about vault."
    assert "- ~~vault digit 1 is 6~~" in page.read_text(encoding="utf-8")
    assert fake_model.requests == []
    a.ask("archive that")  # she knows what "that" is: the model handles it
    await finish(a)
    assert len(fake_model.requests) == 1


async def test_a_code_question_answers_from_notes_without_a_lookup(env, tmp_path, fake_model):
    wiki = FakeWiki()
    a = _with_wiki(env, wiki)
    a2, _, _ = notes_env(env, tmp_path)
    assert a2 is a
    a.ask("write down gate code 3258")
    await finish(a)
    a.ask("What was the gate code?")
    await finish(a)
    assert wiki.questions == []
    assert "gate code 3258" in str(fake_model.requests[-1])


async def test_write_that_down_after_an_answer_saves_her_answer(env, tmp_path, fake_model):
    a, _, page = notes_env(env, tmp_path)
    a.ask("write down vault digit 1 is 6")
    await finish(a)
    fake_model.reply = "The vault code is 6762."
    a.ask("What's the vault code?")
    await finish(a)
    fake_model.reply = "vault code 6762"
    a.ask("Write that down.")
    await finish(a)
    assert page.read_text(encoding="utf-8").endswith("- vault code 6762\n")
    assert a.history[-1].content == "Got it, I wrote down: vault code 6762."
    prompt = str(fake_model.requests[-1])
    assert "The vault code is 6762." in prompt and "vault digit 1 is 6" in prompt


async def test_a_note_that_points_at_the_notes_is_filled_in(env, tmp_path, fake_model):
    a, _, page = notes_env(env, tmp_path)
    for note in ("vault digit 1 is 6", "vault digit 2 is 7"):
        a.ask(f"write down {note}")
        await finish(a)
    fake_model.reply = '"vault code 67"'
    a.ask("Write down the vault code from the notes.")
    await finish(a)
    assert page.read_text(encoding="utf-8").endswith("- vault code 67\n")


async def test_she_asks_when_she_cant_tell_what_to_write(env, tmp_path, fake_model):
    a, _, _ = notes_env(env, tmp_path)
    fake_model.reply = "UNKNOWN"
    a.ask("Write down that thing")
    await finish(a)
    assert a.history[-1].content == "Sure, what should I write down?"


async def test_dictated_notes_never_go_through_the_model(env, tmp_path, fake_model):
    a, _, page = notes_env(env, tmp_path)
    fake_model.reply = "Sure."
    a.ask("what's up?")
    await finish(a)
    a.ask("write down the password is 4471")
    await finish(a)
    assert page.read_text(encoding="utf-8").endswith("- the password is 4471\n")
    assert len(fake_model.requests) == 1


async def test_a_code_on_an_older_page_is_found(env, tmp_path, fake_model):
    a, _, _ = notes_env(env, tmp_path)
    a.ask("open a page called Test4")
    await finish(a)
    a.ask("write down gate code 3258")
    await finish(a)
    a.ask("start a new page called Today")
    await finish(a)
    a.ask("write down vault code 5526")
    await finish(a)
    a.ask("What was the gate code?")
    await finish(a)
    sent = str(fake_model.requests[-1])
    assert 'gate code 3258 (page "Test4")' in sent
    a.ask("What is the vault code?")
    await finish(a)
    assert '(page "Test4")' not in str(fake_model.requests[-1])


async def test_she_writes_a_note_herself(env, tmp_path, fake_model):
    a, events, page = notes_env(env, tmp_path)
    fake_model.reply = "[happy] Got it, noted. [note: vault code 5526]"
    a.ask("The vault code is 55 then 26")
    await finish(a)
    assert page.read_text(encoding="utf-8").endswith("- vault code 5526\n")
    assert {"type": "note_added", "page": "Notes", "text": "vault code 5526"} in events
    assert a.history[-1].content.endswith("[note: vault code 5526]")
    assert "[note:" not in a.history[-1].content.split("noted.")[0]


async def test_she_updates_a_note_by_crossing_off_and_writing(env, tmp_path, fake_model):
    a, _, page = notes_env(env, tmp_path)
    a.ask("write down vault code 6762")
    await finish(a)
    fake_model.reply = "Updated. [cross off: vault] [note: vault code 5526]"
    a.ask("yes, update it")
    await finish(a)
    assert page.read_text(encoding="utf-8").endswith("- ~~vault code 6762~~\n- vault code 5526\n")


async def test_she_uncrosses_a_note(env, tmp_path, fake_model):
    a, _, page = notes_env(env, tmp_path)
    a.ask("write down we're out of jelly")
    await finish(a)
    fake_model.reply = "Done. [cross off: jelly]"
    a.ask("cross that off")
    await finish(a)
    fake_model.reply = "Brought it back. [uncross: out of jelly]"
    a.ask("we are out of jelly though, take the markout off")
    await finish(a)
    assert page.read_text(encoding="utf-8").endswith("- we're out of jelly\n")
    fake_model.reply = "Brought it back. [uncross: gate code]"
    a.ask("unmark the gate code")
    await finish(a)
    assert "couldn't find a crossed-off note about gate code" in a.history[-1].content


async def test_she_opens_a_page_herself(env, tmp_path, fake_model):
    a, _, _ = notes_env(env, tmp_path)
    fake_model.reply = "Switching over. [open page: Escape room]"
    a.ask("let's use the escape room notes")
    await finish(a)
    assert (tmp_path / "notes" / "Escape room.md").is_file()
    assert a._notebook.current_page().stem == "Escape room"


async def test_she_owns_up_when_notes_are_off(env, fake_model):
    a, _, _, _ = env
    fake_model.reply = "Noted. [note: x]"
    a.ask("keep that in mind")
    await finish(a)
    assert a.history[-1].content == "Note taking is turned off in my settings."
    assert [t.content for t in a.history[-3:-1]] == ["keep that in mind", "Noted. [note: x]"]


async def test_renaming_the_page(env, tmp_path, fake_model):
    a, _, page = notes_env(env, tmp_path)
    a.ask("write down vault code 1234")
    await finish(a)
    a.ask("Rename these notes, Haunted House.")
    await finish(a)
    assert a.history[-1].content == "Renamed it to Haunted House."
    assert not page.exists() and (tmp_path / "notes" / "Haunted House.md").is_file()
    fake_model.reply = "Sure, it's now Lighthouse. [rename page: Lighthouse]"
    a.ask("actually make that one Lighthouse")
    await finish(a)
    assert (tmp_path / "notes" / "Lighthouse.md").is_file()
    a.ask("start a new page called Other")
    await finish(a)
    a.ask("call this page Lighthouse")
    await finish(a)
    assert "there's already a page called Lighthouse" in a.history[-1].content


async def test_crossing_off_a_note_on_another_page(env, tmp_path, fake_model):
    a, _, _ = notes_env(env, tmp_path)
    a.ask("open a page called Test4")
    await finish(a)
    a.ask("write down gate code 3258")
    await finish(a)
    a.ask("start a new page called Today")
    await finish(a)
    a.ask("write down gate code 2438")
    await finish(a)
    fake_model.reply = "Done. [cross off: gate 3258]"
    a.ask("could you mark it out?")
    await finish(a)
    test4 = (tmp_path / "notes" / "Test4.md").read_text(encoding="utf-8")
    today = (tmp_path / "notes" / "Today.md").read_text(encoding="utf-8")
    assert "~~gate code 3258~~" in test4 and "~~" not in today
    fake_model.reply = "Done. [cross off: gate 9999]"
    a.ask("and the other one")
    await finish(a)
    assert a.history[-1].content == (
        "Actually, I couldn't find a note about gate 9999 to cross off."
    )


class _Replies:
    """A model that gives each reply in turn (the last one repeats)."""

    def __init__(self, *replies: str):
        self.replies = list(replies)
        self.requests: list = []

    async def stream_chat(self, messages, options):
        self.requests.append(list(messages))
        yield self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


async def test_a_claim_without_a_tag_is_followed_through(env, tmp_path):
    a, _, page = notes_env(env, tmp_path)
    a.ask("write down gate code 3258")
    await finish(a)
    a._model = model = _Replies("I have crossed off the gate code 3258.", "[cross off: gate 3258]")
    a.ask("we used the gate one, get rid of it")
    await finish(a)
    assert "~~gate code 3258~~" in page.read_text(encoding="utf-8")
    assert len(model.requests) == 2
    assert "nothing\nhappened" in model.requests[1][-1]["content"]
    assert a.history[-1].content.endswith("[cross off: gate 3258]")


async def test_no_repair_when_she_meant_nothing(env, tmp_path):
    a, _, _ = notes_env(env, tmp_path)
    a._model = model = _Replies("I've added a bit of thyme, it's lovely.", "NONE")
    a.ask("what goes well with lamb?")
    await finish(a)
    assert len(model.requests) == 2
    assert a.history[-1].content == "I've added a bit of thyme, it's lovely."


async def test_no_repair_for_ordinary_replies(env, tmp_path):
    a, _, _ = notes_env(env, tmp_path)
    a._model = model = _Replies("Lamb goes well with rosemary.")
    a.ask("what goes well with lamb?")
    await finish(a)
    assert len(model.requests) == 1


async def test_she_knows_which_page_was_open_before(env, tmp_path, fake_model):
    a, _, _ = notes_env(env, tmp_path)
    for name in ("Test4", "Haunted House"):
        a.ask(f"open a page called {name}")
        await finish(a)
        a.ask("write down x")
        await finish(a)
    a.ask("Rename these notes labyrinth.")
    await finish(a)
    a.ask("The one we had open before this one.")
    await finish(a)
    pages = next(
        m["content"]
        for m in fake_model.requests[-1]
        if "most recently written first" in m["content"]
    )
    assert "labyrinth (in use now)" in pages
    assert "Test4 (in use before this one, first one this session)" in pages


async def test_she_knows_the_first_page_this_session(env, tmp_path, fake_model):
    a, _, _ = notes_env(env, tmp_path)
    for name in ("HauntTest", "Haunted House", "labyrinth"):
        a.ask(f"open a page called {name}")
        await finish(a)
        a.ask("write down x")
        await finish(a)
    a.ask("Which page did we start on?")
    await finish(a)
    pages = next(
        m["content"]
        for m in fake_model.requests[-1]
        if "most recently written first" in m["content"]
    )
    assert "HauntTest (first one this session)" in pages
    assert "Haunted House (in use before this one)" in pages


async def test_go_to_a_page_that_does_not_exist_goes_to_her(env, tmp_path, fake_model):
    a, _, _ = notes_env(env, tmp_path)
    a.ask("open a page called Test4")
    await finish(a)
    a.ask("Go to the page we started with.")
    await finish(a)
    assert not (tmp_path / "notes" / "we started with.md").exists()
    assert len(fake_model.requests) == 1
    a.ask("switch to the page called Test4")
    await finish(a)
    assert a.history[-1].content == "Back on Test4."


async def test_first_page_goes_to_the_first_one_this_session(env, tmp_path, fake_model):
    a, _, _ = notes_env(env, tmp_path)
    for name in ("HauntTest", "Haunted House", "Jelly"):
        a.ask(f"open a page called {name}")
        await finish(a)
        a.ask("write down x")
        await finish(a)
    a.ask("Can you go to the first page?")
    await finish(a)
    assert a._notebook._current.stem == "HauntTest"
    assert fake_model.requests == []


async def test_first_page_with_none_used_goes_to_her(env, tmp_path, fake_model):
    a, _, _ = notes_env(env, tmp_path)
    a.ask("Can you go to the first page?")
    await finish(a)
    assert len(fake_model.requests) == 1
