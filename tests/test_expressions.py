import pytest
import yaml

from secretary.expressions import TagFilter, strip_tags
from secretary.persona import PersonaError, load_persona
from secretary.prompt import build_messages
from tests.conftest import ROOT


def run(chunks):
    f = TagFilter()
    out = "".join(f.feed(c)[0] for c in chunks) + f.flush()
    return out, f.moods


def test_leading_tag_is_stripped_and_reported():
    assert run(["[happy] Good news!"]) == ("Good news!", ["happy"])


def test_tag_split_across_tokens():
    assert run(["[", "sur", "prised", "]", " You", " got", " it."]) == (
        "You got it.",
        ["surprised"],
    )


def test_no_tag_passes_text_through():
    assert run(["It's at ", "ten."]) == ("It's at ten.", [])


def test_unknown_leading_tag_means_neutral():
    assert run(["[smug] Told you."]) == ("Told you.", ["neutral"])


def test_tag_is_case_and_space_tolerant():
    assert run(["  [ Sad ]  Sorry, it's a no."]) == ("Sorry, it's a no.", ["sad"])


def test_mid_reply_mood_change_leaves_no_double_space():
    assert run(["Done. ", "[happy] ", "Also, you got the job."]) == (
        "Done. Also, you got the job.",
        ["happy"],
    )


def test_ordinary_brackets_mid_reply_are_kept():
    assert run(["[relaxed] See item [2] and [notes]."]) == (
        "See item [2] and [notes].",
        ["relaxed"],
    )


def test_unclosed_bracket_is_released():
    assert run(["[happy] a [very long aside without a close"]) == (
        "a [very long aside without a close",
        ["happy"],
    )


def test_strip_tags():
    assert strip_tags("[happy] Great. [sad] Oh no.") == "Great. Oh no."


def test_persona_fills_missing_moods_with_defaults(tmp_path):
    raw = yaml.safe_load((ROOT / "personas" / "juniper.yaml").read_text())
    raw["expressions"] = {"happy": {"preset": "relaxed", "weight": 0.3}}
    p = tmp_path / "p.yaml"
    p.write_text(yaml.safe_dump(raw))
    persona = load_persona(p)
    assert persona.expression("happy").preset == "relaxed"
    assert persona.expression("sad").preset == "sad"
    assert persona.expression(None).preset == "neutral"


@pytest.mark.parametrize("bad", [{"angry": {"preset": "angry"}}, {"happy": {"preset": "joy"}}])
def test_persona_rejects_unknown_moods_and_presets(tmp_path, bad):
    raw = yaml.safe_load((ROOT / "personas" / "juniper.yaml").read_text())
    raw["expressions"] = bad
    p = tmp_path / "p.yaml"
    p.write_text(yaml.safe_dump(raw))
    with pytest.raises(PersonaError):
        load_persona(p)


def test_prompt_asks_for_a_mood_tag(persona):
    msgs = build_messages(persona, None, [], "hi")
    assert "[happy]" in msgs[0]["content"] and "mood tag" in msgs[-2]["content"]
