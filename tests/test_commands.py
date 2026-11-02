import datetime as dt

import pytest

from secretary.briefs import BriefType, parse_brief
from secretary.commands import BriefRequest, brief_request, describe, find_brief
from tests.conftest import SAMPLES

M, A, E = BriefType.MORNING, BriefType.AFTERNOON, BriefType.EVENING


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Can you read this morning's brief?", BriefRequest(M, "today")),
        ("read me the brief", BriefRequest()),
        ("Juniper, please read the latest brief again", BriefRequest()),
        ("replay yesterday's evening brief", BriefRequest(E, "yesterday")),
        ("go over the afternoon briefing", BriefRequest(A)),
        ("read the sample brief", BriefRequest(sample=True)),
        ("could you read tonight's brief", BriefRequest(E, "today")),
    ],
)
def test_asking_to_hear_a_brief(text, expected):
    assert brief_request(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "What did the brief say about Northwind?",
        "Did you read the brief?",
        "Read the email from Dana that's quoted in the brief",  # about an email
        "When is my coffee chat?",
        "I think the brief was pretty short today",
    ],
)
def test_other_questions_go_to_the_model(text):
    assert brief_request(text) is None


def _brief(name: str, **changes):
    b = parse_brief((SAMPLES / name).read_text(encoding="utf-8"))
    return b.model_copy(update=changes)


def test_find_prefers_real_briefs_and_matches_type_and_day():
    day = dt.date(2026, 11, 2)
    sample = _brief("2026-11-02_morning.md")  # sample: true
    real = _brief("2026-11-02_morning.md", id="real", sample=False)
    older = _brief(
        "2026-11-02_morning.md", id="older", sample=False, date=day - dt.timedelta(days=1)
    )
    evening = _brief("2026-11-02_evening.md", sample=False)
    briefs = [sample, real, older, evening]

    assert find_brief(BriefRequest(M, "today"), briefs, day).id == "real"
    assert find_brief(BriefRequest(M, "yesterday"), briefs, day).id == "older"
    assert find_brief(BriefRequest(sample=True), briefs, day) is sample
    assert find_brief(BriefRequest(), briefs, day) is evening  # newest
    assert find_brief(BriefRequest(A, "today"), briefs, day) is None


def test_describe_for_missing_briefs():
    assert describe(BriefRequest(M, "today")) == "a morning brief from today"
    assert describe(BriefRequest()) == "any briefs"
    assert describe(BriefRequest(sample=True)) == "a sample brief"
