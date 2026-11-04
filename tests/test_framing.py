import asyncio
import shutil

import pytest

from secretary.assistant import Assistant
from secretary.briefs import parse_brief
from secretary.config import FramingConfig
from secretary.framing import (
    BriefFramer,
    FrameLine,
    build_framing_messages,
    check_line,
    paragraphs,
    parse_line,
)
from secretary.state import StateStore
from secretary.tts import SilentSpeaker
from secretary.watcher import BriefScanner
from tests.conftest import SAMPLES, FakeModel

FRAMING = (
    "OPENER: [happy] Okay, today is actually a good one.\n"
    "AFTER 1: [happy] Finally, a real reply from a recruiter.\n"
    "AFTER 3: [sad] Their loss, honestly.\n"
    "CLOSER: [relaxed] Alright, that's me done.\n"
)


@pytest.fixture
def brief(morning_text):
    return parse_brief(morning_text)


def test_parse_line_forms():
    assert parse_line("OPENER: [happy] Hi there.") == (
        "opener",
        None,
        FrameLine("Hi there.", "happy"),
    )
    assert parse_line("**After 2:** [sad] Rough.") == ("after", 2, FrameLine("Rough.", "sad"))
    assert parse_line("after [3]: Nice.") == ("after", 3, FrameLine("Nice.", None))
    assert parse_line('Closer: [weird] "Done."') == ("closer", None, FrameLine("Done.", "neutral"))
    assert parse_line("Here are your lines:") is None
    assert parse_line("OPENER: [happy]") is None  # nothing to say


@pytest.mark.parametrize(
    "line, why",
    [
        ("Okay, busy one today.", ""),
        ("Contoso Data finally wrote back.", ""),  # name from the brief
        ("Honestly, Cooper, that's a quiet day.", ""),  # the user's name
        ("The call is at 10.", "has a number"),
        ("Three new matches, not bad.", "has a number"),
        ("Nice, Globex wants to talk.", "unknown name 'Globex'"),
        ("Nice. Globex wants to talk.", ""),  # sentence start: can't tell, allowed
        ("**Big** day.", "has markup"),
        ("word " * 30, "too long"),
    ],
)
def test_check_line(line, why, brief):
    assert check_line(line, brief.spoken, ["Juniper", "Cooper"], 18) == why


def test_prompt_numbers_paragraphs_and_uses_persona(persona, brief):
    msgs = build_framing_messages(persona, brief, FramingConfig())
    assert "bubbly and upbeat" in msgs[0]["content"]  # follows the loaded persona
    assert "Never change, drop, soften" in msgs[0]["content"]  # core rules stay
    count = len(paragraphs(brief.spoken))
    assert f"[{count}] And the job search" in msgs[1]["content"]
    assert "two AFTER lines" in msgs[1]["content"]
    none = build_framing_messages(persona, brief, FramingConfig(asides=0))
    assert "AFTER" not in none[1]["content"]


async def test_framer_keeps_good_lines_and_drops_bad_ones(persona, brief):
    model = FakeModel(
        "OPENER: [happy] Okay, today is actually a good one.\n"
        "AFTER 1: [happy] Contoso at ten, love it.\n"  # number: dropped
        "AFTER 2: [relaxed] Honestly, Initech can wait.\n"  # invented name: dropped
        "AFTER 9: [relaxed] Hm.\n"  # no such paragraph
        "AFTER 3: [sad] Their loss, honestly.\n"
        "CLOSER: [relaxed] Alright, that's me done."
    )
    framer = BriefFramer(model, persona, brief, FramingConfig())
    framer.start()
    assert (await framer.opener()).text == "Okay, today is actually a good one."
    assert await framer.aside(1) is None
    assert await framer.aside(2) is None
    assert (await framer.aside(3)).mood == "sad"
    assert (await framer.closer()).text == "Alright, that's me done."
    assert set(framer.framing.asides) == {3}


async def test_framer_caps_asides(persona, brief):
    framer = BriefFramer(FakeModel(FRAMING), persona, brief, FramingConfig(asides=1))
    framer.start()
    await framer.closer()
    assert set(framer.framing.asides) == {1}


class SlowModel(FakeModel):
    async def stream_chat(self, messages, options):
        self.requests.append(list(messages))
        await asyncio.sleep(10)
        yield "OPENER: [happy] Too late."


@pytest.fixture
def make_env(tmp_path, persona):
    def make(model, config):
        briefs = tmp_path / "Secretary" / "briefs"
        briefs.mkdir(parents=True, exist_ok=True)
        (tmp_path / "Secretary" / "escalations").mkdir(exist_ok=True)
        shutil.copy(SAMPLES / "2026-11-02_morning.md", briefs)
        events = []

        async def emit(e):
            events.append(e)

        a = Assistant(
            persona=persona,
            model=model,
            speaker=SilentSpeaker(),
            scanner=BriefScanner(briefs),
            state=StateStore(tmp_path / "state.json"),
            escalations_dir=tmp_path / "Secretary" / "escalations",
            emit=emit,
            framing=config,
        )
        return a, events

    return make


async def read(a):
    await a.poll()
    a.start_brief()
    await asyncio.wait_for(a._task, 5)


async def test_readout_with_framing(make_env, morning_text):
    a, events = make_env(FakeModel(FRAMING), FramingConfig())
    await read(a)
    said = [e["text"] for e in events if e["type"] == "say"]
    assert said[0] == "Okay, today is actually a good one."
    assert said[1].startswith("Heads up, this is a practice brief")
    assert said[-1] == "Alright, that's me done."
    assert "Their loss, honestly." in said
    # Every word of the brief is still said, unchanged and in order.
    brief_said = [s for s in said if s not in FRAMING]
    assert " ".join(brief_said).split() == parse_brief(morning_text).spoken.split()
    moods = [e["mood"] for e in events if e["type"] == "expression"]
    assert moods == ["happy", "happy", "sad", "relaxed"]
    assert events[-1]["type"] == "speech_done"


async def test_slow_model_falls_back_to_plain_readout(make_env, morning_text):
    a, events = make_env(SlowModel(), FramingConfig(wait_seconds=0.05))
    await read(a)
    said = [e["text"] for e in events if e["type"] == "say"]
    assert " ".join(said).split() == parse_brief(morning_text).spoken.split()
    assert [e["mood"] for e in events if e["type"] == "expression"] == ["neutral"]


async def test_framing_off_never_calls_model(make_env):
    model = FakeModel(FRAMING)
    a, events = make_env(model, FramingConfig(enabled=False))
    await read(a)
    assert model.requests == []


async def test_waiting_on_a_cancelled_framer_does_not_cancel_the_reader(persona, brief):
    framer = BriefFramer(SlowModel(), persona, brief, FramingConfig(wait_seconds=5))
    framer.start()
    await asyncio.sleep(0)
    framer.cancel()  # e.g. it timed out on the opener
    assert await framer.aside(1) is None  # must not raise CancelledError
    assert await framer.closer() is None


@pytest.mark.parametrize(
    "raw, kind, n, text, mood",
    [
        ("[sad] OPENER: Rough one.", "opener", None, "Rough one.", "sad"),
        ("OPENER - [relaxed] Quiet day.", "opener", None, "Quiet day.", "relaxed"),
        ("- **AFTER paragraph 2**: [happy] Nice.", "after", 2, "Nice.", "happy"),
        ("Closer — [neutral] That's it.", "closer", None, "That's it.", "neutral"),
    ],
)
def test_parse_line_tolerates_other_layouts(raw, kind, n, text, mood):
    assert parse_line(raw) == (kind, n, FrameLine(text, mood))


async def test_unlabeled_lines_still_give_opener_and_closer(persona, brief):
    # Real gemma4:12b output that skipped the labels.
    model = FakeModel(
        "[relaxed] Okay, bit of a mixed bag this morning.\n"
        "[happy] That sounds like a real shot.\n"
        "[sad] Their loss.\n"
        "Alright, that's the lot."
    )
    framer = BriefFramer(model, persona, brief, FramingConfig())
    framer.start()
    assert (await framer.opener()) == FrameLine("Okay, bit of a mixed bag this morning.", "relaxed")
    assert (await framer.closer()) == FrameLine("Alright, that's the lot.", None)
    assert framer.framing.asides == {}  # can't tell which paragraph they were for


async def test_preamble_is_not_an_opener(persona, brief):
    framer = BriefFramer(
        FakeModel("Here are my lines:\n" + FRAMING), persona, brief, FramingConfig()
    )
    framer.start()
    assert (await framer.opener()).text == "Okay, today is actually a good one."
