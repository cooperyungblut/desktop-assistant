from __future__ import annotations

import pytest

from secretary.lookup import first_name, follow_up, lookup_request, render, usable
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
        "In which year did the Dutch East India Company cease operations?",
        "By whom was the Eiffel Tower designed?",
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
        "In which room is my interview?",
        "In the morning remind me to call",
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


def test_usable_gives_one_article_two_slots_while_others_wait():
    request = lookup_request("What is the longest river in Africa?")
    found = [
        Passage("Africa", "opening", 1.0, "title"),
        Passage("Africa", "best", 1.0, "title"),
        Passage("Orange River", "near miss", 1.0, "all"),
        Passage("Africa", "facts", 1.0, "title"),
        Passage("Nile", "answer", 1.0, "all"),
    ]
    kept = usable(found, request)
    assert [p.title for p in kept] == ["Africa", "Africa", "Orange River", "Nile"]
    # With nothing else to offer, the article gets the spare slot back.
    assert len(usable(found[:2] + found[3:4], request)) == 3


def test_usable_keeps_titles_and_multiword_all_matches_only():
    request = lookup_request("What is the Roman Empire?")
    found = [_p("A", "title"), _p("B", "all"), _p("C", "any")]
    assert [p.title for p in usable(found, request)] == ["A", "B"]


def test_single_keyword_all_matches_are_not_enough():
    request = lookup_request("What is a cage?")
    assert [p.title for p in usable([_p("A", "all"), _p("B", "title")], request)] == ["B"]


def test_forced_lookup_accepts_weak_matches_and_caps_the_count():
    request = lookup_request("look up quasars")
    found = [_p(c, "any") for c in "ABCDEF"]
    assert len(usable(found, request)) == 4


def test_render_clips_long_passages_at_a_sentence():
    long = "First sentence here. " + ("Another one follows it. " * 60)
    text = render([Passage("Title", long, 1.0, "title")])
    assert text.startswith("[Title] First sentence here.")
    assert len(text) < 700 and text.endswith(".")


@pytest.mark.parametrize(
    "question",
    ["Is Keanu Reeves married?", "Does Keanu Reeves have any kids?", "Was Rome founded in 753 BC?"],
)
def test_yes_no_questions_are_looked_up(question):
    assert lookup_request(question) is not None


@pytest.mark.parametrize("question", ["Is my interview today?", "Do you like music?"])
def test_personal_yes_no_questions_are_not(question):
    assert lookup_request(question) is None


def test_follow_up_adds_the_last_topic_only_when_pointing_back():
    assert follow_up("What are those four species?", "StarCraft") == (
        "What are those four species? (StarCraft)"
    )
    assert follow_up("Does he have kids?", "Keanu Reeves") == "Does he have kids? (Keanu Reeves)"
    assert follow_up("What year did that come out?", "The Matrix") == (
        "What year did that come out? (The Matrix)"
    )
    assert follow_up("Who directed the film that is set in Rome?", "The Matrix") == (
        "Who directed the film that is set in Rome?"
    )
    assert follow_up("What is the capital of France?", "StarCraft") == (
        "What is the capital of France?"
    )
    assert follow_up("Who made StarCraft and its sequel?", "StarCraft") == (
        "Who made StarCraft and its sequel?"
    )
    assert follow_up("What are those?", None) == "What are those?"


def test_he_and_his_point_at_the_name_in_her_last_answer():
    reply = "Keanu Reeves starred as Neo in the film. This information comes from Wikipedia."
    assert follow_up("What is his birthday?", "The Matrix", reply) == (
        "What is his birthday? (Keanu Reeves)"
    )
    # "it" still means the article she looked up, not the person
    assert follow_up("When did it come out?", "The Matrix", reply) == (
        "When did it come out? (The Matrix)"
    )
    # no name in the answer: fall back to the article
    assert follow_up("Who is she?", "Mona Lisa", "It is a painting.") == "Who is she? (Mona Lisa)"


@pytest.mark.parametrize(
    ("reply", "name"),
    [
        ("Keanu Reeves starred as Neo.", "Keanu Reeves"),
        ("The Matrix stars Keanu Reeves and Carrie-Anne Moss.", "Keanu Reeves"),
        ("This Information is odd. Hugo Weaving played Smith.", "Hugo Weaving"),
        ("It came out in 1999.", None),
    ],
)
def test_first_name_in_a_reply(reply, name):
    assert first_name(reply) == name


@pytest.mark.parametrize(
    "question",
    [
        "What is 8 times 12?",
        "What is 13? Divided by 3.",
        "What is the square root of 144?",
        "What is the derivative of x to the third?",
        "What is 15% of 80?",
        "What is the smallest prime number greater than 100?",
        "What is 2^10?",
    ],
)
def test_math_is_worked_out_not_looked_up(question):
    assert lookup_request(question) is None


@pytest.mark.parametrize(
    "question",
    [
        "What is a derivative in finance?",
        "What is a prime number?",
        "When did World War 2 end?",
        "Who was the 16th president of the United States?",
        "What happened on 9/11?",
        "What happened in Europe in 1939-1945?",
    ],
)
def test_questions_with_numbers_are_still_looked_up(question):
    assert lookup_request(question) is not None
