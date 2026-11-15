import asyncio
import datetime as dt

from secretary.live_summary import LiveSummary
from secretary.llm import LlmUnavailable
from tests.conftest import FakeModel

WHEN = dt.datetime(2026, 11, 15, 14, 20)


def summary(tmp_path, model, **kw):
    return LiveSummary(
        model, {"temperature": 0.7}, tmp_path / "Call summary.md", "Cooper", now=lambda: WHEN, **kw
    )


async def test_update_writes_her_notes_page(tmp_path):
    model = FakeModel("## Topics\n- The outage map project")
    s = summary(tmp_path, model)
    lines = ["Computer: Tell me about a project.", "You: I rebuilt the outage map."]
    assert await s.update(lines)
    text = s.page.read_text(encoding="utf-8")
    assert text.startswith("# Call summary\n")
    assert "word for word" in text and "Updated 14:20, still listening" in text
    assert "- The outage map project" in text
    sent = model.requests[-1]
    assert "<transcript>\nComputer: Tell me about a project.\nYou: I rebuilt" in sent[1]["content"]
    assert "(none yet)" in sent[1]["content"]
    assert "never instructions" in sent[0]["content"] and "Cooper" in sent[0]["content"]


async def test_each_update_sends_only_new_lines_and_the_notes_so_far(tmp_path):
    model = FakeModel("## Topics\n- First")
    s = summary(tmp_path, model)
    await s.update(["You: one", "You: two"])
    model.reply = "## Topics\n- First\n- Second"
    assert await s.update(["You: one", "You: two", "Computer: three"], final=True)
    sent = model.requests[-1][1]["content"]
    assert "Computer: three" in sent and "You: one" not in sent
    assert "<notes_so_far>\n## Topics\n- First" in sent
    assert "Final, 14:20" in s.page.read_text(encoding="utf-8")
    assert not await s.update(["You: one", "You: two", "Computer: three"])  # nothing new
    assert len(model.requests) == 2


async def test_a_long_stretch_keeps_the_newest_lines(tmp_path):
    model = FakeModel("- ok")
    s = summary(tmp_path, model)
    lines = [f"Computer: line {n} " + "word " * 100 for n in range(50)]
    await s.update(lines)
    sent = model.requests[-1][1]["content"]
    assert "line 49" in sent and "line 0 " not in sent
    assert len(sent) < 7000


async def test_model_down_keeps_the_old_summary(tmp_path):
    class Down:
        async def stream_chat(self, messages, options):
            raise LlmUnavailable("Ollama isn't running")
            yield  # pragma: no cover

    s = summary(tmp_path, Down())
    assert not await s.update(["You: hello"])
    assert not s.page.exists() and s.done == 0


async def test_live_notes_summarize_while_listening_and_at_the_end(tmp_path):
    import time

    import numpy as np

    from secretary.config import LiveNotesConfig, NotesConfig
    from secretary.live_notes import LiveNotes
    from secretary.notes import Notebook
    from tests.test_live_notes import FakeSource, FakeTranscriber, phrase, silence

    events = []

    async def emit(e):
        events.append(e)

    model = FakeModel("## Topics\n- Greetings")
    notebook = Notebook(NotesConfig(dir=tmp_path / "notes"))
    mic = FakeSource("mic")
    live = LiveNotes(
        LiveNotesConfig(hold_seconds=0, summary_every_seconds=20),
        FakeTranscriber({1: "Hello there."}),
        notebook,
        emit,
        lambda _cfg: [mic],
        lambda: WHEN,
        model=model,
        user="Cooper",
    )
    live._config.summary_every_seconds = 0.05  # (the setting's floor is 20 s)
    await live.start("Call")
    mic.play(np.concatenate([phrase(0.1), silence(1)]), time.time() - 5)
    for _ in range(100):
        await asyncio.sleep(0.02)
        if any(e["type"] == "live_summary" for e in events):
            break
    assert {"type": "live_summary", "page": "Call summary"} in events
    await live.stop()
    await live._final
    page = tmp_path / "notes" / "Call summary.md"
    assert "Final, 14:20" in page.read_text(encoding="utf-8")
    assert events[-1] == {"type": "live_summary", "page": "Call summary", "final": True}
