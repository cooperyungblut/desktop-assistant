"""When she looks something up in Wikipedia, and what the model is shown.

A question is looked up when it asks for general knowledge (who, what, when,
where, why, how, "tell me about ...") and is not about Cooper's own day, or when
the user says so outright ("look up ...", "what does Wikipedia say about ...").
This is a plain pattern match in code, like the calendar and brief commands, so
the model never decides whether to search. The passages go to the model as data
for that one question and are not kept in the chat history.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from .wiki import Passage, query_terms

MAX_PASSAGES = 4
# Passages from one article, while other articles have something to offer.
PER_ARTICLE = 2
PASSAGE_CHARS = 600  # per passage shown to the model: four of these are ~600 tokens

_EXPLICIT = re.compile(
    r"\b(look\s*up|search\s+(?:for|up|wikipedia)|(?:on|in|from|says?|said)\s+wiki(?:pedia)?"
    r"|wiki(?:pedia)?\s+(?:says?|for|on|about))\b",
    re.IGNORECASE,
)
_ASKS = re.compile(
    r"^\s*(?:(?:hey|ok|okay|so|and|um|uh|well|now|then)\b[,\s]*)*"
    r"(who|whom|whose|what|what's|whats|when|where|why|how|which|"
    # yes/no questions: "Is Keanu Reeves married?", "Does he have kids?"
    r"is|are|was|were|does|do|did|has|have|had|"
    r"tell\s+me\s+about|explain|define|describe)\b",
    re.IGNORECASE,
)
# Questions about the user's own day or about her, which Wikipedia can't answer.
_PERSONAL = re.compile(
    r"\b(my|mine|i|i'm|i've|i'll|me|you|you're|your|yours|we|our|brief|briefing|email|emails|"
    r"calendar|schedule|agenda|interview|meeting|appointment|application|today|tonight|"
    r"tomorrow|yesterday|this\s+(?:morning|afternoon|evening|week)|"
    # someone getting back to the user: "Did Dana reply?", "Has the recruiter called?"
    r"reply|replied|respond|responded|wrote\s+back|called|texted|messaged|heard\s+back|"
    r"recruiter)\b",
    re.IGNORECASE,
)
# Live information she doesn't have: better to say so than quote an article.
_LIVE = re.compile(
    r"\b(weather|forecast|news|headlines|stocks?|traffic|right\s+now|latest|currently|"
    r"what\s+time\s+is\s+it|what\s+day\s+is\s+it|what(?:'s|s|\s+is)\s+the\s+(?:time|date)|"
    r"scores?\s+of|live)\b",
    re.IGNORECASE,
)
_COMMAND_WORDS = re.compile(
    r"\b(please|can\s+you|could\s+you|look\s*up|search\s+(?:for|up|wikipedia)|"
    r"(?:on|in|from)\s+wiki(?:pedia)?|wiki(?:pedia)?|what\s+does\s+\w+\s+say\s+about|"
    r"says?\s+about|for\s+me)\b",
    re.IGNORECASE,
)


class Lookup(Protocol):
    """Anything that can find passages for a question (see secretary.wiki.WikiIndex)."""

    def search(self, question: str, limit: int = 3) -> list[Passage]: ...


# A follow-up that points back at the last topic ("What are those four species?").
_BACK_REFERENCE = re.compile(
    r"\b(those|these|it|its|they|them|their|theirs)\b"
    # "that" on its own ("What year did that come out?"), not "the film that ..."
    r"|\bthat\b(?!\s+(?:is|was|are|were|the|a|an|has|had|of)\b)",
    re.IGNORECASE,
)
_PERSON_REFERENCE = re.compile(r"\b(he|him|his|she|her|hers)\b", re.IGNORECASE)
# Two or more capitalised words in a row: "Keanu Reeves", "Carrie-Anne Moss".
_NAME = re.compile(r"\b[A-Z][\w'\u2019.-]+(?:\s+[A-Z][\w'\u2019.-]+)+")
_NOT_A_NAME_START = {"The", "This", "That", "These", "Those", "It", "In", "On", "According"}


def first_name(text: str) -> str | None:
    """The first name of a person or thing in a reply, if any ("Keanu Reeves
    starred as Neo." -> "Keanu Reeves")."""
    for match in _NAME.finditer(text):
        words = match.group().split()
        if words[0] in _NOT_A_NAME_START:
            words = words[1:]
        if len(words) >= 2:
            return " ".join(words)
    return None


def follow_up(question: str, topic: str | None, last_reply: str | None = None) -> str:
    """The question with what it points back at added, so the search can find it.

    "he/his/her" -> the first name in her last answer ("What is his birthday?"
    after "Keanu Reeves starred as Neo" -> "... (Keanu Reeves)"), falling back to
    the last article she looked up; "it/that/those/they" -> that article
    ("What are those four species?" after StarCraft -> "... (StarCraft)").
    """
    subject = None
    if _PERSON_REFERENCE.search(question):
        subject = (last_reply and first_name(last_reply)) or topic
    elif _BACK_REFERENCE.search(question):
        subject = topic
    if subject and subject.lower() not in question.lower():
        return f"{question} ({subject})"
    return question


@dataclass(frozen=True)
class LookupRequest:
    query: str  # the question with any "look up" wording taken out
    forced: bool  # the user asked for a lookup by name


def lookup_request(text: str) -> LookupRequest | None:
    """A `LookupRequest` if this question should be looked up, else None."""
    if _EXPLICIT.search(text):
        query = _COMMAND_WORDS.sub(" ", text)
        return LookupRequest(query=" ".join(query.split()).strip(" ?!.,") or text, forced=True)
    if len(text.split()) > 30 or not _ASKS.match(text):
        return None
    rest = re.sub(r"\btell\s+me\s+about\b", " ", text, flags=re.IGNORECASE)
    if _PERSONAL.search(rest) or _LIVE.search(text):
        return None
    return LookupRequest(query=text, forced=False)


def usable(passages: Sequence[Passage], request: LookupRequest) -> list[Passage]:
    """The passages worth showing the model.

    Always: articles the question names. All-keywords matches only when the
    question had at least two keywords (one common word matches anything).
    Any-keyword matches only when the user asked for a lookup by name.
    """
    terms = query_terms(request.query)
    keep = []
    for passage in passages:
        if (
            passage.match == "title"
            or (passage.match == "all" and len(terms) >= 2)
            or request.forced
        ):
            keep.append(passage)
    # No article takes more than PER_ARTICLE slots ahead of the others: "the
    # longest river in Africa" needs the Nile, not three passages of "Africa".
    first: list[Passage] = []
    spare: list[Passage] = []
    taken: dict[str, int] = {}
    for passage in keep:
        taken[passage.title] = taken.get(passage.title, 0) + 1
        (first if taken[passage.title] <= PER_ARTICLE else spare).append(passage)
    return (first + spare)[:MAX_PASSAGES]


def render(passages: Sequence[Passage]) -> str:
    """The passages as the model sees them: '[Title] text', cut to a fixed size."""
    return "\n".join(f"[{p.title}] {_clip(p.text.replace(chr(10), ' '))}" for p in passages)


def _clip(text: str) -> str:
    if len(text) <= PASSAGE_CHARS:
        return text
    cut = text[:PASSAGE_CHARS]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[: end + 1] if end > PASSAGE_CHARS // 2 else cut.rstrip() + "..."
