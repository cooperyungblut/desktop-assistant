"""Years and times written as digits, rewritten the way people say them
(speech only). She writes digits, so the chat shows "1909" and "10:30".

Kokoro reads "1909" as "nineteen zero nine" and "2005" as "twenty zero five".
People say "nineteen oh nine" and "two thousand five". Her chat text keeps the
digits; only the text handed to the voice is rewritten.

Only years with a 0 in the tens place are rewritten (Kokoro already says
"nineteen sixty-four" and "nineteen hundred" fine), and only where the number
reads as a year: after "in", "since", a month and so on, before "BC"/"AD", in a
range ("1901-1909"), or at the end of a clause. "1909 meters" is left alone.

Clock times: "10:30" -> "ten thirty", "9:05" -> "nine oh five", "10:00" ->
"ten o'clock", "7:00 pm" -> "seven PM".
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


_SMALL = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
_TENS = {2: "twenty", 3: "thirty", 4: "forty", 5: "fifty"}


def _under_60(n: int) -> str:
    if n < 20:
        return _SMALL[n]
    tens, ones = divmod(n, 10)
    return _TENS[tens] + (f"-{_SMALL[ones]}" if ones else "")


# 10:30, 9:05 am, 14:00, 7:45 P.M.
_TIME = re.compile(
    r"(?<![\d:.])\b([01]?\d|2[0-3]):([0-5]\d)\b(?![:.]\d)"
    r"(?:\s*([AaPp])\.?\s?[Mm]\b\.?)?"
)


def say_time(hour: int, minute: int, half: str | None = None) -> str:
    """10:30 -> "ten thirty", 9:05 -> "nine oh five", 10:00 am -> "ten AM"."""
    words = [_under_60(hour)]
    if minute == 0:
        words.append("" if half else "o'clock")
    elif minute < 10:
        words.append(f"oh {_SMALL[minute]}")
    else:
        words.append(_under_60(minute))
    if half:
        words.append(f"{half.upper()}M")
    return " ".join(w for w in words if w)


def spoken_times(text: str) -> str:
    """`text` with its "10:30"-style times written out as they're spoken."""

    def replace(m: re.Match[str]) -> str:
        said = say_time(int(m.group(1)), int(m.group(2)), m.group(3))
        # "P.M." may end the sentence: keep its full stop.
        return said + "." if m.group(3) and m.group(0).endswith(".") else said

    return _TIME.sub(replace, text)


def spoken_numbers(text: str) -> str:
    """Years and clock times the way a person says them (for the voice only)."""
    return spoken_times(spoken_years(text))
