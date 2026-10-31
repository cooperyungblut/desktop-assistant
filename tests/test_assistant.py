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
    assert events[-1]["type"] == "say"
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
    assert events[-2]["type"] == "reply_end" or events[-1]["type"] == "say"
    assert a.history[-1].content == "It's at ten. Have fun!"


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
