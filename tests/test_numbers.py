from __future__ import annotations

import pytest

from secretary.numbers import say_year, spoken_years


@pytest.mark.parametrize(
    ("year", "said"),
    [
        (1909, "nineteen oh nine"),
        (1101, "eleven oh one"),
        (1805, "eighteen oh five"),
        (2001, "two thousand one"),
        (2009, "two thousand nine"),
    ],
)
def test_say_year(year, said):
    assert say_year(year) == said


@pytest.mark.parametrize(
    ("text", "spoken"),
    [
        ("He was born in 1909.", "He was born in nineteen oh nine."),
        (
            "It was founded on 20 March 1602 and dissolved in 1799.",
            "It was founded on 20 March sixteen oh two and dissolved in 1799.",
        ),
        (
            "The company closed on 31 December 1909.",
            "The company closed on 31 December nineteen oh nine.",
        ),
        ("Born January 7, 1907, in Ohio.", "Born January 7, nineteen oh seven, in Ohio."),
        ("The war ran 1803-1805.", "The war ran eighteen oh three-eighteen oh five."),
        ("It opened 1907.", "It opened nineteen oh seven."),
        ("In 2005 she moved.", "In two thousand five she moved."),
        ("It sold 1909 copies in a week", None),
        ("The peak is 1909 meters high.", None),
        ("It costs $1909 today.", None),
        ("Version 1.1909 shipped.", None),
        ("About 12,1909 people.", None),
        ("It fell 1909% overnight.", None),
        ("He was born in 1964.", None),  # Kokoro reads these fine already
        (
            "Wikipedia says 1908's flood was the worst.",
            "Wikipedia says nineteen oh eight's flood was the worst.",
        ),
    ],
)
def test_spoken_years(text, spoken):
    assert spoken_years(text) == (spoken or text)
