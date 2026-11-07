import pytest

from secretary.briefs import load_brief
from secretary.evaluation import Case, Summary, failures, load_cases, run_case, score, table
from tests.conftest import ROOT, SAMPLES, FakeModel

CASES = ROOT / "evals" / "cases.yaml"


def test_cases_file_is_valid_and_uses_real_sample_briefs():
    cases = load_cases(CASES)
    assert len(cases) >= 10
    for c in cases:
        assert (SAMPLES / f"{c.brief}.md").is_file(), c.id


def test_every_must_group_needs_one_alternative():
    case = Case(id="x", brief="b", question="q", must=[["tuesday", "tue"], ["one", "1"]])
    assert score(case, "Tuesday at one works.", "happy")["missing"] == []
    assert score(case, "Tuesday works.", "happy")["missing"] == [["one", "1"]]


def test_short_words_match_whole_words_only():
    case = Case(id="x", brief="b", question="q", must=[["one"], ["3"]])
    assert score(case, "Someone said 30.", None)["missing"] == [["one"], ["3"]]
    assert score(case, "At one, item 3.", None)["missing"] == []


def test_forbidden_and_unknown():
    case = Case(id="x", brief="b", question="q", must_not=["$", "dollars"], unknown=True)
    guess = score(case, "It pays $90,000 a year.", "happy")
    assert guess["forbidden"] == ["$"] and guess["said_unknown"] is False
    honest = score(case, "I don't see pay in the brief.", "neutral")
    assert honest["forbidden"] == [] and honest["said_unknown"] is True


def test_mood_must_be_present_and_expected():
    case = Case(id="x", brief="b", question="q", moods=["sad", "neutral"])
    assert score(case, "a", "sad")["mood_ok"]
    assert not score(case, "a", "happy")["mood_ok"]
    assert not score(case, "a", None)["mood_ok"]


async def test_run_case_strips_tag_and_scores(persona):
    brief = load_brief(SAMPLES / "2026-11-02_morning.md")
    case = Case(
        id="northwind",
        brief=brief.id,
        question="Did Northwind pick me?",
        must=[["passed", "not selected"]],
        moods=["sad"],
    )
    good = await run_case(FakeModel("[sad] Sorry, Northwind passed on it."), persona, brief, case)
    assert good.answer == "Sorry, Northwind passed on it." and good.mood == "sad"
    assert good.facts_ok and good.mood_ok

    bad = await run_case(FakeModel("Great news, they picked you!"), persona, brief, case)
    assert not bad.facts_ok and not bad.tag_ok

    s = Summary("fake", [good, bad])
    assert s.facts == pytest.approx(0.5) and s.tags == pytest.approx(0.5)
    assert "fake" in table([s])
    assert any("no mood tag" in line for line in failures(s))


async def test_model_errors_are_reported_not_raised(persona):
    class Broken:
        async def stream_chat(self, messages, options):
            raise RuntimeError("boom")
            yield  # pragma: no cover

    brief = load_brief(SAMPLES / "2026-11-02_morning.md")
    r = await run_case(Broken(), persona, brief, Case(id="x", brief=brief.id, question="q"))
    assert r.error == "boom" and not r.facts_ok


def test_suggesting_claude_always_fails():
    case = Case(id="x", brief="b", question="q", unknown=True)
    result = score(case, "I don't see that. Want me to check with Claude?", "neutral")
    assert result["said_unknown"] is True and result["forbidden"] == ["check with claude"]


async def test_history_turns_come_before_the_question(persona):
    brief = load_brief(SAMPLES / "2026-11-02_morning.md")
    case = Case.model_validate(
        {
            "id": "sure",
            "brief": "b",
            "question": "Are you sure?",
            "history": [
                {"role": "user", "content": "Is Keanu Reeves married?"},
                {"role": "assistant", "content": "[relaxed] I don't think so."},
            ],
            "must": [["best guess"]],
        }
    )
    model = FakeModel("[neutral] Not 100%, but it's my best guess.")
    result = await run_case(model, persona, brief, case)
    sent = model.requests[-1]
    assert [m["content"] for m in sent[-4:-2]] == [
        "Is Keanu Reeves married?",
        "[relaxed] I don't think so.",
    ]
    assert sent[-1]["content"] == "Are you sure?" and result.facts_ok
