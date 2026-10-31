import datetime as dt

import pytest

from secretary import escalations as esc

NOW = dt.datetime(2026, 11, 2, 10, 15, 3, tzinfo=dt.timezone(dt.timedelta(hours=-6)))


@pytest.mark.parametrize(
    "text",
    [
        "/claude did Dana say if it's technical?",
        "Can you check with Claude whether the role is remote?",
        "ask Claude about the salary range",
        "please verify that with claude",
    ],
)
def test_explicit_requests_escalate(text):
    assert esc.wants_escalation(text)


@pytest.mark.parametrize(
    "text",
    [
        "what time is my coffee chat?",
        "is Claude a good name for a cat?",  # mentions Claude, doesn't ask to escalate
        "I'll check it myself",
    ],
)
def test_ordinary_questions_do_not(text):
    assert not esc.wants_escalation(text)


def test_strip_command():
    assert esc.strip_command("/claude: did Dana reply?") == "did Dana reply?"
    assert esc.strip_command("check with Claude about X") == "check with Claude about X"


def test_round_trip(tmp_path):
    e = esc.new_escalation(
        "Is the phone screen technical?",
        about_brief="2026-11-02_morning",
        context=[("Cooper", "what's the\nContoso thing?"), ("Juniper", "A phone screen!")],
        now=NOW,
    )
    path = esc.write(e, tmp_path)
    assert path.name == "esc-20261102-101503.md"
    text = path.read_text(encoding="utf-8")
    assert "status: open" in text and "# Question" in text

    back = esc.parse(text)
    assert back.id == e.id and back.question == e.question
    assert back.context == [("Cooper", "what's the Contoso thing?"), ("Juniper", "A phone screen!")]


def test_mark_answered_is_idempotent(tmp_path):
    e = esc.new_escalation("Q?", about_brief=None, now=NOW)
    esc.write(e, tmp_path)

    assert esc.mark_answered(tmp_path, [e.id, "esc-unknown"], "2026-11-02_afternoon") == [e.id]
    back = esc.parse((tmp_path / f"{e.id}.md").read_text(encoding="utf-8"))
    assert back.status is esc.Status.ANSWERED
    assert back.answered_in == "2026-11-02_afternoon"
    assert esc.mark_answered(tmp_path, [e.id], "2026-11-02_evening") == []
