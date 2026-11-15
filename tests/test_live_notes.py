import asyncio
import datetime as dt
import time

import numpy as np
import pytest

from secretary.config import LiveNotesConfig, NotesConfig
from secretary.live_audio import SAMPLE_RATE
from secretary.live_notes import (
    LiveNotesUnavailable,
    LiveSession,
    is_echo,
    make_sources,
    page_name,
)
from secretary.notes import Notebook
from secretary.stt import SttUnavailable


def phrase(level, seconds=1.0):
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    return (level * np.sin(2 * np.pi * 200 * t)).astype(np.float32)


def silence(seconds):
    return np.zeros(int(seconds * SAMPLE_RATE), dtype=np.float32)


class FakeSource:
    def __init__(self, source, fail=None):
        self.source = source
        self.error = None
        self.fail = fail
        self.handler = None
        self.stopped = False

    def start(self, on_block):
        if self.fail:
            raise RuntimeError(self.fail)
        self.handler = on_block

    def stop(self):
        self.stopped = True

    def play(self, audio, start, block=0.1):
        n, size = 0, int(block * SAMPLE_RATE)
        while n < audio.size:
            part = audio[n : n + size]
            n += part.size
            self.handler(part, start + n / SAMPLE_RATE)


class FakeTranscriber:
    """Says the text for each phrase's loudness (0.1 -> texts[1], 0.2 -> texts[2], ...)."""

    available = True

    def __init__(self, texts):
        self.texts = texts
        self.prompts = []

    async def transcribe_samples(self, samples, prompt=None):
        self.prompts.append(prompt)
        key = round(float(np.max(np.abs(samples))) * 10)
        return self.texts.get(key, "")


@pytest.fixture
def notebook(tmp_path):
    return Notebook(NotesConfig(dir=tmp_path / "notes"))


def session(notebook, texts, sources=None, **kw):
    sources = sources or [FakeSource("mic"), FakeSource("computer")]
    # Lines are held until stop() unless a test says otherwise, so the order on
    # the page doesn't depend on how fast this machine transcribes.
    cfg = LiveNotesConfig(**{"hold_seconds": 15.0, **kw})
    return LiveSession(cfg, FakeTranscriber(texts), notebook, sources, tick_seconds=0.01), sources


def page_lines(path):
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("-")]


async def test_both_sides_are_written_word_for_word_in_time_order(notebook, tmp_path):
    s, (mic, computer) = session(
        notebook,
        {1: "So tell me about a project you're proud of.", 2: "Sure, the outage map rebuild."},
    )
    page = tmp_path / "notes" / "Interview.md"
    await s.start(page)
    t0 = time.time() - 20
    computer.play(np.concatenate([phrase(0.1, 2), silence(1)]), t0)
    mic.play(np.concatenate([silence(3.5), phrase(0.2, 2), silence(1)]), t0)
    await s.stop()
    stamp = dt.datetime.fromtimestamp(t0)
    assert page_lines(page) == [
        f"- [{stamp:%H:%M}] **Computer:** So tell me about a project you're proud of.",
        f"- [{dt.datetime.fromtimestamp(t0 + 3.5):%H:%M}] **You:** Sure, the outage map rebuild.",
    ]
    assert [label for _, label in s.lines] == ["Computer", "You"]
    assert mic.stopped and computer.stopped


async def test_lines_are_written_while_listening(notebook, tmp_path):
    s, (mic, _) = session(notebook, {3: "Testing one two."}, hold_seconds=0)
    page = tmp_path / "notes" / "Live.md"
    await s.start(page)
    mic.play(np.concatenate([phrase(0.3), silence(1)]), time.time() - 3)
    for _ in range(100):
        await asyncio.sleep(0.01)
        if page.exists() and page_lines(page):
            break
    assert page_lines(page)[0].endswith("**You:** Testing one two.")
    await s.stop()


async def test_a_mic_line_that_repeats_the_call_is_left_out(notebook, tmp_path):
    s, (mic, computer) = session(
        notebook, {1: "So what drew you to this role?", 2: "so what drew you to this role"}
    )
    page = tmp_path / "notes" / "Echo.md"
    await s.start(page)
    t0 = time.time() - 10
    computer.play(np.concatenate([phrase(0.1, 2), silence(1)]), t0)
    mic.play(np.concatenate([silence(0.1), phrase(0.2, 2), silence(1)]), t0)
    await s.stop()
    assert [line.split("** ", 1)[0] for line in page_lines(page)] == [
        f"- [{dt.datetime.fromtimestamp(t0):%H:%M}] **Computer:"
    ]


async def test_an_echo_right_after_you_talk_doesnt_join_your_line(notebook, tmp_path):
    s, (mic, computer) = session(
        notebook,
        {
            1: "I rebuilt the outage map.",
            2: "What was the hardest part?",
            3: "what was the hardest part",
        },
    )
    page = tmp_path / "notes" / "Echo2.md"
    await s.start(page)
    t0 = time.time() - 10
    computer.play(np.concatenate([silence(1.8), phrase(0.2), silence(1)]), t0)
    mic.play(np.concatenate([phrase(0.1), silence(0.8), phrase(0.3), silence(1)]), t0)
    await s.stop()
    assert [line.split("] ", 1)[1] for line in page_lines(page)] == [
        "**You:** I rebuilt the outage map.",
        "**Computer:** What was the hardest part?",
    ]


async def test_echo_check_can_be_turned_off(notebook, tmp_path):
    s, (mic, computer) = session(
        notebook,
        {1: "So what drew you to this role?", 2: "so what drew you to this role"},
        echo_check=False,
    )
    page = tmp_path / "notes" / "Echo.md"
    await s.start(page)
    t0 = time.time() - 10
    computer.play(np.concatenate([phrase(0.1, 2), silence(1)]), t0)
    mic.play(np.concatenate([silence(0.1), phrase(0.2, 2), silence(1)]), t0)
    await s.stop()
    assert len(page_lines(page)) == 2


async def test_one_speakers_back_to_back_chunks_join_into_one_line(notebook, tmp_path):
    s, (mic, _) = session(notebook, {1: "At my last job", 2: "I rebuilt the outage map."})
    page = tmp_path / "notes" / "Join.md"
    await s.start(page)
    # A 1 s pause ends a chunk (pause_seconds 0.7) but joins the line (JOIN_GAP 1.5).
    audio = np.concatenate([phrase(0.1), silence(1.0), phrase(0.2), silence(1)])
    mic.play(audio, time.time() - 10)
    await s.stop()
    assert [line.split("** ", 1)[1] for line in page_lines(page)] == [
        "At my last job I rebuilt the outage map."
    ]


async def test_whisper_gets_what_that_side_said_before(notebook, tmp_path):
    s, (mic, _) = session(notebook, {1: "First part.", 2: "Second part."})
    await s.start(tmp_path / "notes" / "Prompt.md")
    mic.play(np.concatenate([phrase(0.1), silence(3), phrase(0.2), silence(1)]), time.time() - 10)
    await s.stop()
    assert s._transcriber.prompts == ["", " First part."]


async def test_one_missing_device_still_listens_to_the_other(notebook, tmp_path):
    sources = [FakeSource("mic"), FakeSource("computer", fail="no loopback device")]
    s, _ = session(notebook, {1: "Hello?"}, sources=sources)
    await s.start(tmp_path / "notes" / "Half.md")
    assert s.listening == ["mic"]
    assert s.problems == ["Computer: no loopback device"]
    await s.stop()


async def test_no_devices_at_all_is_an_error(notebook, tmp_path):
    sources = [FakeSource("mic", fail="no mic"), FakeSource("computer", fail="no speaker")]
    s, _ = session(notebook, {}, sources=sources)
    with pytest.raises(LiveNotesUnavailable, match="no mic"):
        await s.start(tmp_path / "notes" / "None.md")
    assert not s.running


async def test_needs_whisper_loaded(notebook, tmp_path):
    s, _ = session(notebook, {})
    s._transcriber.available = False
    with pytest.raises(LiveNotesUnavailable, match="speech recognition"):
        await s.start(tmp_path / "notes" / "X.md")


async def test_transcription_errors_are_reported_not_fatal(notebook, tmp_path):
    class Broken(FakeTranscriber):
        async def transcribe_samples(self, samples, prompt=None):
            raise SttUnavailable("speech recognition is still loading")

    cfg = LiveNotesConfig(hold_seconds=0)
    mic = FakeSource("mic")
    s = LiveSession(cfg, Broken({}), notebook, [mic], tick_seconds=0.01)
    await s.start(tmp_path / "notes" / "Err.md")
    mic.play(np.concatenate([phrase(0.1), silence(1)]), time.time() - 5)
    await s.stop()
    assert s.problems == ["speech recognition is still loading"]
    assert s.lines == []


async def test_recent_audio_is_kept_while_listening_and_dropped_after(notebook, tmp_path):
    s, (mic, _) = session(notebook, {}, recent_seconds=2)
    await s.start(tmp_path / "notes" / "Buf.md")
    mic.play(phrase(0.1, 5), time.time() - 5)
    assert 2 * SAMPLE_RATE <= s.recent["mic"].last().size < 3 * SAMPLE_RATE
    await s.stop()
    assert s.recent["mic"].last().size == 0


async def test_live_lines_go_to_their_own_page_not_the_one_in_use(notebook, tmp_path):
    notebook.new_page("Groceries")
    s, (mic, _) = session(notebook, {1: "Hello there."})
    page = tmp_path / "notes" / "Call.md"
    await s.start(page)
    mic.play(np.concatenate([phrase(0.1), silence(1)]), time.time() - 5)
    await s.stop()
    assert notebook.current_page().stem == "Groceries"
    assert page_lines(page)[0].endswith("**You:** Hello there.")


@pytest.mark.parametrize(
    "mic, computer, echo",
    [
        ("so what drew you to this role", "So, what drew you to this role?", True),
        ("drew you to this role", "So what drew you to this role? Take your time.", True),
        ("I think it was the team", "So what drew you to this role?", False),
        ("yes", "yes, and then what?", False),  # too short to call
        ("", "anything", False),
    ],
)
def test_is_echo(mic, computer, echo):
    assert is_echo(mic, computer) is echo


def test_page_name():
    when = dt.datetime(2026, 11, 15, 14, 2)
    assert page_name("Live notes {date} {time}", when) == "Live notes 2026-11-15 1402"


def test_sources_need_the_live_extra(monkeypatch):
    import builtins

    real = builtins.__import__

    def no_soundcard(name, *args, **kw):
        if name == "soundcard":
            raise ImportError("No module named 'soundcard'")
        return real(name, *args, **kw)

    monkeypatch.setattr(builtins, "__import__", no_soundcard)
    with pytest.raises(LiveNotesUnavailable, match="--extra live"):
        make_sources(LiveNotesConfig())


# ---- starting and stopping -----------------------------------------------------

from secretary.live_notes import LiveNotes, LiveRequest, live_request, tag_request  # noqa: E402


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Start live notes", LiveRequest("start")),
        ("Okay, can you start taking live notes?", LiveRequest("start")),
        (
            "Can you start live notes for the Acme interview?",
            LiveRequest("start", "Acme interview"),
        ),
        ("start live notes hidden", LiveRequest("start", "", True)),
        (
            "Start live notes for the Acme interview and stay hidden",
            LiveRequest("start", "Acme interview", True),
        ),
        ("take a live transcript in the background", LiveRequest("start", "", True)),
        ("Transcribe this call.", LiveRequest("start")),
        ("Please take notes of this meeting", LiveRequest("start")),
        ("start transcribing", LiveRequest("start")),
        ("listen in on this interview quietly", LiveRequest("start", "", True)),
        ("Stop live notes.", LiveRequest("stop")),
        ("stop transcribing now", LiveRequest("stop")),
        ("Okay, stop listening", LiveRequest("stop")),
        ("transcribe the following sentence", None),
        ("take notes", None),
        ("what's a live transcript?", None),
        ("stop", None),
    ],
)
def test_live_request(text, expected):
    assert live_request(text) == expected


@pytest.mark.parametrize(
    "tag, expected",
    [
        ("start", LiveRequest("start")),
        ("start hidden", LiveRequest("start", "", True)),
        ("start, Acme interview", LiveRequest("start", "Acme interview")),
        ("stop", LiveRequest("stop")),
        ("off", LiveRequest("stop")),
    ],
)
def test_tag_request(tag, expected):
    assert tag_request(tag) == expected


def live_notes(notebook, events, sources=None, texts=None, **kw):
    async def emit(e):
        events.append(e)

    made = sources if sources is not None else [FakeSource("mic"), FakeSource("computer")]
    cfg = LiveNotesConfig(**{"hold_seconds": 0.0, **kw})
    clock = dt.datetime(2026, 11, 15, 14, 2)
    return LiveNotes(
        cfg, FakeTranscriber(texts or {}), notebook, emit, lambda _cfg: made, lambda: clock
    ), made


async def test_live_notes_start_on_a_new_page_in_use_and_stop(notebook):
    events = []
    live, (mic, _) = live_notes(notebook, events, texts={1: "Hello there."})
    session = await live.start()
    assert live.on and session.page.name == "Live notes 2026-11-15 1402.md"
    assert notebook.current_page() == session.page  # questions and notes use it
    assert events[-1] == {
        "type": "live_notes",
        "on": True,
        "hidden": False,
        "page": "Live notes 2026-11-15 1402",
        "lines": 0,
    }
    assert await live.start() is session  # already on: the same session
    mic.play(np.concatenate([phrase(0.1), silence(1)]), time.time() - 5)
    assert await live.stop() is session
    assert not live.on
    assert events[-1]["on"] is False and events[-1]["lines"] == 1
    assert {"type": "live_line", "label": "You", "text": "Hello there."} in events
    assert await live.stop() is None


async def test_live_notes_off_in_config(notebook):
    live, _ = live_notes(notebook, [], enabled=False)
    with pytest.raises(LiveNotesUnavailable, match="turned off"):
        await live.start()


async def test_her_own_voice_is_left_out(notebook, tmp_path):
    s, (mic, computer) = session(
        notebook,
        {
            1: "Okay, I'm taking live notes.",
            2: "okay I'm taking live notes",
            3: "Hi, can you hear me?",
        },
    )
    page = tmp_path / "notes" / "Own.md"
    await s.start(page)
    now = time.time()
    s.own_speech(True, "Okay, I'm taking live notes.")
    # Through the loopback, and quieter through the mic (speakers, no headphones).
    computer.play(np.concatenate([phrase(0.1, 1.5)]), now - 0.2)
    mic.play(np.concatenate([phrase(0.2, 1.5)]), now - 0.2)
    s.own_speech(False)
    computer.play(np.concatenate([silence(1.5), phrase(0.3), silence(1)]), now + 1.3)
    await s.stop()
    assert [line.split("] ", 1)[1] for line in page_lines(page)] == [
        "**Computer:** Hi, can you hear me?"
    ]


async def test_a_failed_start_leaves_no_page_behind(notebook):
    sources = [FakeSource("mic", fail="no mic"), FakeSource("computer", fail="no speaker")]
    live, _ = live_notes(notebook, [], sources=sources)
    with pytest.raises(LiveNotesUnavailable):
        await live.start()
    assert not notebook.dir.exists() or not list(notebook.dir.glob("*.md"))
    assert not live.on


async def test_stopping_never_cuts_off_a_line_being_written(notebook, tmp_path):
    slow_write = notebook.write

    def write(*args):
        time.sleep(0.05)  # a slow disk, or a slow CI machine
        return slow_write(*args)

    notebook.write = write
    s, (mic, _) = session(notebook, {1: "First.", 2: "Second."}, hold_seconds=0)
    page = tmp_path / "notes" / "Slow.md"
    await s.start(page)
    mic.play(np.concatenate([phrase(0.1), silence(3)]), time.time() - 10)
    await asyncio.sleep(0.05)  # the ticker picks up the first line
    mic.play(np.concatenate([phrase(0.2), silence(1)]), time.time() - 4)
    await s.stop()
    assert [line.split("** ", 1)[1] for line in page_lines(page)] == ["First.", "Second."]
    assert len(s.lines) == 2
