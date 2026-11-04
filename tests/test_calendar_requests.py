import datetime as dt

import pytest

from secretary.calendar_requests import (
    AgendaRequest,
    EventDraft,
    Problem,
    RemoveRequest,
    answer_to_confirm,
    day_phrase,
    describe_agenda_item,
    describe_event,
    is_calendar_request,
    parse_agenda,
    parse_event,
    parse_removal,
)

NOW = dt.datetime(2026, 11, 4, 15, 0)  # a Wednesday afternoon
TODAY = NOW.date()


def said(text: str) -> str:
    """Parse and read back, the way she'd say it."""
    result = parse_event(text, NOW, name="Juniper")
    assert isinstance(result, EventDraft), result
    return describe_event(result, TODAY)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Add dentist appointment Thursday at 3pm to my calendar",
            "Dentist appointment, Thursday, November 5, 3 to 4 PM",
        ),
        (
            "Put lunch with Sam on my calendar tomorrow at noon",
            "Lunch with Sam, Thursday, November 5, noon to 1 PM",
        ),
        (
            "Schedule a call with the Northwind recruiter November 12 at 10:30 for 30 minutes",
            "Call with the Northwind recruiter, Thursday, November 12, 10:30 to 11 AM",
        ),
        (
            "Add to my calendar: interview prep Friday 2 to 4",
            "Interview prep, Friday, November 6, 2 to 4 PM",
        ),
        (
            "Hey Juniper, can you add dinner at Olive Garden tomorrow at 7 to my calendar please",
            "Dinner at Olive Garden, Thursday, November 5, 7 to 8 PM",
        ),
        (
            "add mom's birthday to my calendar on December 3rd",
            "Mom's birthday, Thursday, December 3, all day",
        ),
        (
            "add gym to my calendar next monday at 6 in the morning",
            "Gym, Monday, November 9, 6 to 7 AM",
        ),
        (
            "add a meeting to my calendar from 11 to 1 next thursday",
            "Meeting, Thursday, November 12, 11 AM to 1 PM",
        ),
        (
            "add standup to my calendar at 9:15 for 15 minutes",
            "Standup, Thursday, November 5, 9:15 to 9:30 AM",
        ),
        (
            "put the career fair on my calendar for the 20th from 9am until 5pm",
            "Career fair, Friday, November 20, 9 AM to 5 PM",
        ),
        (
            "add movie night to my calendar tonight at 8",
            "Movie night, Wednesday, November 4, 8 to 9 PM",
        ),
        (
            "add a call to my calendar 11/18 at 4:45 p.m.",
            "Call, Wednesday, November 18, 4:45 to 5:45 PM",
        ),
        ("book a haircut Saturday at 10", "Haircut, Saturday, November 7, 10 to 11 AM"),
        (
            "Pencil in coffee with Dana for Friday at 9 o'clock",
            "Coffee with Dana, Friday, November 6, 9 to 10 AM",
        ),
        (
            "add a review to my calendar Friday for an hour and a half at 1",
            "Review, Friday, November 6, 1 to 2:30 PM",
        ),
        ("add a party to my calendar Saturday 9 to 1", "Party, Saturday, November 7, 9 AM to 1 PM"),
        (
            "add a flight to my calendar january 8 at 6:40 am",
            "Flight, Friday, January 8, 2027, 6:40 to 7:40 AM",
        ),
        (
            "add a late shift to my calendar friday 10pm to 2am",
            "Late shift, Friday, November 6, 10 PM to 2 AM on Saturday",
        ),
    ],
)
def test_reads_back_the_event(text, expected):
    assert said(text) == expected


def test_a_time_without_a_day_is_the_next_time_it_comes_round():
    # It's 3 PM: "at 5" is later today, "at 3" (3 PM) has just passed so it's tomorrow.
    assert said("add a call to my calendar at 5") == "Call, Wednesday, November 4, 5 to 6 PM"
    assert said("add the dentist to my calendar at 3") == "Dentist, Thursday, November 5, 3 to 4 PM"


def test_weekdays():
    # Plain or "this": the soonest one, today included. "Next": in next week.
    assert said("add x to my calendar wednesday") == "X, Wednesday, November 4, all day"
    assert said("add x to my calendar this friday") == "X, Friday, November 6, all day"
    assert said("add x to my calendar next friday") == "X, Friday, November 13, all day"
    assert said("add x to my calendar next monday") == "X, Monday, November 9, all day"


def test_a_passed_date_means_next_year_or_month():
    assert said("add x to my calendar march 1") == "X, Monday, March 1, 2027, all day"
    assert said("add x to my calendar on the 2nd") == "X, Wednesday, December 2, all day"


def test_default_length_is_configurable():
    result = parse_event("add x to my calendar friday at 2", NOW, default_minutes=30)
    assert result.end - result.start == dt.timedelta(minutes=30)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("add to my calendar", "What should I call it?"),
        ("add lunch to my calendar", "When is it?"),
        ("add something to my calendar next week", "Which day is that?"),
        ("add a call to my calendar for 30 minutes on friday", "What time does it start?"),
        (
            "add Sam's party to my calendar February 30",
            "I don't think February 30 is a real date. Which day is it?",
        ),
        (
            "add team sync to my calendar Wednesday, November 12 at 2",
            "November 12 is a Thursday, not a Wednesday. Which day did you mean?",
        ),
        (
            "add lunch to my calendar tomorrow on friday",
            "I heard two different days, Thursday, November 5 and Friday, November 6. "
            "Which one is it?",
        ),
    ],
)
def test_asks_instead_of_guessing(text, message):
    assert parse_event(text, NOW) == Problem(message)


@pytest.mark.parametrize(
    "text",
    [
        "what's on my calendar tomorrow",
        "did you add the dentist to my calendar?",
        "don't put that on my calendar",
        "add some salt to the recipe",
        "read me this morning's brief",
        "is my calendar free on friday",
    ],
)
def test_not_a_calendar_request(text):
    assert not is_calendar_request(text, "Juniper")
    assert parse_event(text, NOW) is None


@pytest.mark.parametrize(
    ("text", "answer"),
    [
        ("yes", True),
        ("Yeah, go ahead.", True),
        ("okay", True),
        ("Juniper, yes please", True),
        ("no", False),
        ("Nope, cancel that", False),
        ("wait, make it four", False),
        ("what's the weather", None),
        ("yes and also tell me about the coffee chat with the recruiter tomorrow", None),
    ],
)
def test_answer_to_confirm(text, answer):
    assert answer_to_confirm(text, "Juniper") is answer


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Remove the test event from my calendar", RemoveRequest("test")),
        (
            "hey Juniper, delete the dentist appointment on friday from my calendar",
            RemoveRequest("dentist appointment", dt.date(2026, 11, 6)),
        ),
        (
            "take lunch with Sam off my calendar tomorrow",
            RemoveRequest("lunch with Sam", dt.date(2026, 11, 5)),
        ),
        ("cancel everything on my calendar tomorrow", RemoveRequest("", dt.date(2026, 11, 5))),
        ("clear my calendar for Friday", RemoveRequest("", dt.date(2026, 11, 6))),
        (
            "remove the career fair event from the calendar on the 20th",
            RemoveRequest("career fair", dt.date(2026, 11, 20)),
        ),
        ("remove it from my calendar", Problem("Which event should I remove?")),
    ],
)
def test_removal_requests(text, expected):
    assert parse_removal(text, NOW, name="Juniper") == expected


@pytest.mark.parametrize(
    "text",
    [
        "cancel that",  # no "calendar": that's an answer to a read-back, not a removal
        "delete it",
        "what's on my calendar",
        "don't remove the dentist from my calendar",
        "add lunch to my calendar tomorrow at noon",
    ],
)
def test_not_a_removal_request(text):
    assert parse_removal(text, NOW, name="Juniper") is None


def test_a_title_ending_in_off_survives_adding():
    assert said("add day off to my calendar friday") == "Day off, Friday, November 6, all day"


@pytest.mark.parametrize(
    ("text", "day"),
    [
        ("what's on my calendar today", TODAY),
        ("What's on my calendar tomorrow?", dt.date(2026, 11, 5)),
        ("what do I have on my calendar friday", dt.date(2026, 11, 6)),
        ("read me my calendar", TODAY),
        ("hey Juniper, check my calendar for the 20th", dt.date(2026, 11, 20)),
        ("do I have anything on my calendar next monday", dt.date(2026, 11, 9)),
        ("is my calendar free tomorrow afternoon", dt.date(2026, 11, 5)),
        ("show me my calendar for november 12", dt.date(2026, 11, 12)),
    ],
)
def test_agenda_requests(text, day):
    assert parse_agenda(text, NOW, name="Juniper") == AgendaRequest(day)


def test_agenda_asks_when_the_day_is_unclear():
    assert isinstance(parse_agenda("what's on my calendar this week", NOW), Problem)
    assert isinstance(parse_agenda("what's on my calendar tomorrow and friday", NOW), Problem)


@pytest.mark.parametrize(
    "text",
    [
        "what's on tomorrow",  # no "calendar"
        "add lunch to my calendar tomorrow at noon",
        "remove the test event from my calendar",
        "clear my calendar for friday",
        "don't look at my calendar",
        "read me this morning's brief",
    ],
)
def test_not_an_agenda_request(text):
    assert parse_agenda(text, NOW, name="Juniper") is None


def test_agenda_wording():
    assert day_phrase(TODAY, TODAY) == "today"
    assert day_phrase(dt.date(2026, 11, 5), TODAY) == "tomorrow"
    assert day_phrase(dt.date(2026, 11, 6), TODAY) == "on Friday, November 6"
    timed = EventDraft(
        "Coffee chat", TODAY, dt.datetime(2026, 11, 4, 10), dt.datetime(2026, 11, 4, 11)
    )
    assert describe_agenda_item(timed) == "Coffee chat at 10 AM"
    assert describe_agenda_item(EventDraft("Birthday", TODAY)) == "Birthday, all day"
