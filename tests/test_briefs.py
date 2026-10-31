import datetime as dt

import pytest

from secretary.briefs import BriefError, BriefType, load_brief, parse_brief
from tests.conftest import SAMPLES


def test_all_samples_parse():
    for path in SAMPLES.glob("*.md"):
        brief = load_brief(path)
        assert brief.id == path.stem
        assert brief.spoken and brief.display


def test_morning_fields(morning_text):
    brief = parse_brief(morning_text)
    assert brief.type is BriefType.MORNING
    assert brief.date == dt.date(2026, 11, 2)
    assert brief.generated_at.utcoffset() == dt.timedelta(hours=-6)
    assert "Interview invite" in brief.display
    assert brief.spoken.startswith("Good morning")
    assert "# Spoken" not in brief.display


def test_answers_escalations_defaults_to_empty():
    brief = load_brief(SAMPLES / "2026-11-02_evening.md")
    assert brief.answers_escalations == []


def test_bom_and_crlf_are_tolerated(morning_text):
    brief = parse_brief("﻿" + morning_text.replace("\n", "\r\n"))
    assert brief.id == "2026-11-02_morning"


def test_naive_generated_at_gets_a_timezone(morning_text):
    text = morning_text.replace("2026-11-02T09:00:12-06:00", "2026-11-02T09:00:12")
    assert parse_brief(text).generated_at.tzinfo is not None


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda t: t.replace("secretary-brief/1", "secretary-brief/9"), "schema"),
        (lambda t: t.replace("type: morning", "type: midnight"), "invalid brief"),
        (lambda t: t.replace("# Spoken", "## Spoken"), "# Spoken"),
        (lambda t: t.split("# Spoken")[0] + "# Spoken\n", "empty"),
        (lambda t: t.replace("---\n", "", 1), "opening"),
    ],
)
def test_invalid_briefs_are_rejected(morning_text, mutate, message):
    with pytest.raises(BriefError, match=message):
        parse_brief(mutate(morning_text))


def test_id_must_match_file_name(morning_text):
    with pytest.raises(BriefError, match="file name"):
        parse_brief(morning_text, expected_id="2026-11-03_morning")


def test_nested_headings_in_display_are_fine(morning_text):
    brief = parse_brief(morning_text)
    assert "## Calendar today" in brief.display
