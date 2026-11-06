"""Years written as digits, rewritten the way people say them (speech only).

Kokoro reads "1909" as "nineteen zero nine" and "2005" as "twenty zero five".
People say "nineteen oh nine" and "two thousand five". Her chat text keeps the
digits; only the text handed to the voice is rewritten.

Only years with a 0 in the tens place are rewritten (Kokoro already says
"nineteen sixty-four" and "nineteen hundred" fine), and only where the number
reads as a year: after "in", "since", a month and so on, before "BC"/"AD", in a
range ("1901-1909"), or at the end of a clause. "1909 meters" is left alone.
"""

from __future__ import annotations

import re

_ONES = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
_FIRST = {
    11: "eleven",
    12: "twelve",
    13: "thirteen",
    14: "fourteen",
    15: "fifteen",
    16: "sixteen",
    17: "seventeen",
    18: "eighteen",
    19: "nineteen",
}

_BEFORE = (
    "in|since|from|until|till|by|of|during|before|after|around|circa|c\\.|year|born|died|"
    "early|late|mid|between|and|to|"
    "january|february|march|april|may|june|july|august|september|october|november|december|"
    "jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)
# A candidate: 11x0y or 200y, not part of a longer number, a decimal or money.
_YEAR = re.compile(r"(?<![\d$£€.,])\b(1[1-9]0[1-9]|200[1-9])\b(?![\d,.]\d|%)")
_PREV_WORD = re.compile(rf"(?:\b(?:{_BEFORE})|\d{{1,2}},?|[-–—])\s*$", re.IGNORECASE)
_NEXT = re.compile(
    r"^\s*(?:$|[.,;:!?)\]\"'’”]|[-–—]|(?:BC|AD|BCE|CE)\b|'s\b)",
)


def say_year(year: int) -> str:
    """1909 -> "nineteen oh nine", 2005 -> "two thousand five"."""
    if 2001 <= year <= 2009:
        return f"two thousand {_ONES[year - 2001]}"
    return f"{_FIRST[year // 100]} oh {_ONES[year % 10 - 1]}"


def spoken_years(text: str) -> str:
    """`text` with its "1909"-style years written out as they're spoken."""

    def replace(match: re.Match[str]) -> str:
        before, after = text[: match.start()], text[match.end() :]
        if _PREV_WORD.search(before) or _NEXT.match(after):
            return say_year(int(match.group(1)))
        return match.group(0)

    return _YEAR.sub(replace, text)
