from secretary.briefs import load_brief
from secretary.vocabulary import MAX_PROMPT_CHARS, brief_terms, speech_hints
from tests.conftest import SAMPLES


def test_names_come_out_of_the_morning_brief():
    terms = brief_terms(load_brief(SAMPLES / "2026-11-02_morning.md"))
    for name in ["Northwind Traders", "Contoso Data", "Dana Reyes", "Sam Ortiz", "Litware"]:
        assert name in terms


def test_headings_dates_and_sentence_starts_are_left_out():
    terms = brief_terms(load_brief(SAMPLES / "2026-11-02_morning.md"))
    for word in ["Calendar", "Needs", "Every", "Asks", "Tue", "Nov", "Friday", "PM"]:
        assert word not in terms


def test_hints_without_a_brief_still_name_the_people(persona):
    assert speech_hints(None, persona) == "Names: Juniper, Cooper, Claude."


def test_hints_are_capped(persona, morning_text):
    from secretary.briefs import parse_brief

    brief = parse_brief(morning_text)
    brief.display += "\n" + " ".join(f"Company{i} Holdings" for i in range(200))
    hints = speech_hints(brief, persona)
    assert len(hints) <= MAX_PROMPT_CHARS and hints.endswith(".")
