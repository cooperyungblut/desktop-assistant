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
        (1945, "nineteen forty-five"),
        (1900, "nineteen hundred"),
        (1812, "eighteen twelve"),
        (2000, "two thousand"),
        (2026, "twenty twenty-six"),
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
            "It was founded on 20 March sixteen oh two and dissolved in seventeen ninety-nine.",
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
        ("He was born in 1964.", "He was born in nineteen sixty-four."),
        (
            "The war ended in 1945, not 1900.",
            "The war ended in nineteen forty-five, not nineteen hundred.",
        ),
        ("Music of the 1990s and 2000s", "Music of the nineteen nineties and two thousands"),
        ("He worked there 1995s", "He worked there 1995s"),
        ("In 2026 and 2010.", "In twenty twenty-six and twenty ten."),
        ("The peak is 1945 meters high.", None),
        (
            "Wikipedia says 1908's flood was the worst.",
            "Wikipedia says nineteen oh eight's flood was the worst.",
        ),
    ],
)
def test_spoken_years(text, spoken):
    assert spoken_years(text) == (spoken or text)


@pytest.mark.parametrize(
    ("text", "spoken"),
    [
        ("The call is at 10:30.", "The call is at ten thirty."),
        ("Lunch at 9:05 with Dana.", "Lunch at nine oh five with Dana."),
        ("It starts at 10:00.", "It starts at ten o'clock."),
        ("Dinner at 7:00 pm, then 8:15 P.M.", "Dinner at seven PM, then eight fifteen PM."),
        ("From 14:45 to 16:20", "From fourteen forty-five to sixteen twenty"),
        ("Psalm 23:4 and ratio 1.5:2", "Psalm 23:4 and ratio 1.5:2"),
        ("At 12:30:15 exactly", "At 12:30:15 exactly"),
        ("Born in 1909, up at 6:30.", "Born in nineteen oh nine, up at six thirty."),
    ],
)
def test_spoken_numbers_says_times_and_years(text, spoken):
    from secretary.numbers import spoken_numbers

    assert spoken_numbers(text) == spoken
