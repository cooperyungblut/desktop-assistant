from __future__ import annotations

import pytest

from secretary.lookup import lookup_request, render, usable
from secretary.wiki import Passage


@pytest.mark.parametrize(
    "text",
    [
        "What is Nicolas Cage's birthday?",
        "Who wrote Pride and Prejudice?",
        "Why did the Roman Empire fall?",
        "How tall is Mount Everest?",
        "Tell me about the French Revolution",
        "Hey, what's the capital of Australia?",
        "Look up Alan Turing",
        "what does wikipedia say about octopuses",
    ],
)
def test_factual_questions_are_looked_up(text):
    assert lookup_request(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "What time is my interview?",
        "Who emailed me today?",
        "What's on my calendar tomorrow?",
        "What did the brief say about the offer?",
        "How are you?",
        "What's the weather like?",
        "What time is it?",
        "Did Dana reply?",
        "Read me this morning's brief",
        "Thanks, that's all",
        "Add lunch to my calendar",
    ],
)
def test_personal_live_and_chat_questions_are_not(text):
    assert lookup_request(text) is None


def test_explicit_lookup_is_forced_and_strips_the_command():
    request = lookup_request("Can you look up the Hundred Years' War for me?")
    assert request.forced
    assert request.query == "the Hundred Years' War"
    assert lookup_request("What is Python?").forced is False


def test_explicit_lookup_wins_even_when_it_sounds_personal():
    assert lookup_request("look up what my interviewer's company does").forced


def _p(title: str, match: str) -> Passage:
    return Passage(title, f"{title} text.", 1.0, match)


def test_usable_keeps_titles_and_multiword_all_matches_only():
    request = lookup_request("What is the Roman Empire?")
    found = [_p("A", "title"), _p("B", "all"), _p("C", "any")]
    assert [p.title for p in usable(found, request)] == ["A", "B"]


def test_single_keyword_all_matches_are_not_enough():
    request = lookup_request("What is a cage?")
    assert [p.title for p in usable([_p("A", "all"), _p("B", "title")], request)] == ["B"]


def test_forced_lookup_accepts_weak_matches_and_caps_the_count():
    request = lookup_request("look up quasars")
    found = [_p(c, "any") for c in "ABCDE"]
    assert len(usable(found, request)) == 3


def test_render_clips_long_passages_at_a_sentence():
    long = "First sentence here. " + ("Another one follows it. " * 60)
    text = render([Passage("Title", long, 1.0, "title")])
    assert text.startswith("[Title] First sentence here.")
    assert len(text) < 700 and text.endswith(".")
