"""Years and times written as digits, rewritten the way people say them
(speech only). She writes digits, so the chat shows "1909" and "10:30".

Kokoro reads years digit-group by digit-group: "1909" as "nineteen zero nine",
"1945" as "nineteen hundred forty five", "2005" as "twenty zero five". People
say "nineteen oh nine", "nineteen forty-five", "two thousand five". Her chat
text keeps the digits; only the text handed to the voice is rewritten.

Years 1100-2099 are rewritten where the number reads as a year: after "in",
"since", a month and so on, before "BC"/"AD", in a range ("1939-1945"), or at
the end of a clause. "1945 meters" is left alone. Decades too: "1990s" ->
"nineteen nineties".

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

_TENS_WORDS = {
    2: "twenty",
    3: "thirty",
    4: "forty",
    5: "fifty",
    6: "sixty",
    7: "seventy",
    8: "eighty",
    9: "ninety",
}
_TEENS = [
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

_BEFORE = (
    "in|since|from|until|till|by|of|during|before|after|around|circa|c\\.|year|born|died|"
    "early|late|mid|between|and|to|"
    "january|february|march|april|may|june|july|august|september|october|november|december|"
    "jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)
# A candidate year (1100-2099) or decade ("1990s"), not part of a longer number,
# a decimal or money.
_YEAR = re.compile(r"(?<![\d$£€.,])\b(1[1-9]\d\d|20\d\d)(s?)\b(?![\d,.]\d|%)")
_PREV_WORD = re.compile(rf"(?:\b(?:{_BEFORE})|\d{{1,2}},?|[-–—])\s*$", re.IGNORECASE)
_NEXT = re.compile(
    r"^\s*(?:$|[.,;:!?)\]\"'’”]|[-–—]|(?:BC|AD|BCE|CE)\b|'s\b)",
)


def _under_100(n: int) -> str:
    if n < 10:
        return _ONES[n - 1]
    if n < 20:
        return _TEENS[n - 10]
    tens, ones = divmod(n, 10)
    return _TENS_WORDS[tens] + (f"-{_ONES[ones - 1]}" if ones else "")


def _plural(words: str) -> str:
    """ "nineteen ninety" -> "nineteen nineties", "... hundred" -> "... hundreds"."""
    if words.endswith("y"):
        return words[:-1] + "ies"
    return words + "s"


def say_year(year: int, decade: bool = False) -> str:
    """1909 -> "nineteen oh nine", 1945 -> "nineteen forty-five", 1900 ->
    "nineteen hundred", 2005 -> "two thousand five", 2026 -> "twenty twenty-six".
    With decade: 1990 -> "nineteen nineties"."""
    century, rest = divmod(year, 100)
    if 2000 <= year <= 2009:
        words = "two thousand" + (f" {_ONES[rest - 1]}" if rest else "")
    else:
        first = "twenty" if century == 20 else _FIRST[century]
        if rest == 0:
            words = f"{first} hundred"
        elif rest < 10:
            words = f"{first} oh {_ONES[rest - 1]}"
        else:
            words = f"{first} {_under_100(rest)}"
    return _plural(words) if decade else words


def spoken_years(text: str) -> str:
    """`text` with its "1909"-style years written out as they're spoken."""

    def replace(match: re.Match[str]) -> str:
        year, decade = int(match.group(1)), bool(match.group(2))
        if decade:
            if year % 10 == 0:
                return say_year(year, decade=True)
            return match.group(0)
        before, after = text[: match.start()], text[match.end() :]
        if _PREV_WORD.search(before) or _NEXT.match(after):
            return say_year(year)
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
