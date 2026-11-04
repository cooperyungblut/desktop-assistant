"""Turn "add lunch with Sam to my calendar tomorrow at noon" into an event.

Like brief requests and escalation, this is parsed in code, not by the local
model: the date and time of an event must never depend on a model doing date
arithmetic. The parser finds the date, time and duration phrases, resolves
them against the current date, and whatever is left becomes the title. She
then reads the event back and only adds it after a yes, so a misheard word
costs one "no", not a wrong calendar entry.

Supported phrasing (case doesn't matter):
    dates     today, tonight, tomorrow, the day after tomorrow, Friday,
              this Friday, next Friday, November 12, Nov 12th, 12 November,
              the 12th, 11/12 (month/day)
    times     at 3, 3pm, 3:30 p.m., at noon, 15:00, 3 o'clock, plus "in the
              morning", "in the evening", "tomorrow afternoon" as am/pm hints
    ranges    from 2 to 4, 2-4pm, 11 to 1, 9am until 5pm
    duration  for 30 minutes, for an hour, for 2 hours, for an hour and a half
A time without am/pm is read as business hours: 8 to 11 is morning, 12 to 7
is afternoon or evening. No time at all makes an all-day event.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from dataclasses import dataclass

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip
_MONTH = (
    r"(?P<month>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|"
    r"aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
)
_WEEKDAY = r"(?P<wd>monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
_PART = r"(?:\s+(?P<part>morning|afternoon|evening|night))?"
_ORD = r"(?:st|nd|rd|th)?"

# ---- recognising a request -------------------------------------------------

_VERB = r"(?:add|put|schedule|book|create|make|set\s+up|pencil|block|throw|stick)"
_QUESTION_START = re.compile(
    r"^\s*(what|when|who|where|why|how|which|did|does|do|is|was|are|have|has)\b", re.I
)
_NEGATION = re.compile(r"\b(don't|dont|do\s+not|never|no\s+need)\b", re.I)
_NEW_EVENT = (
    r"(?:add|create|make|set\s+up|put\s+in)\s+(?:me\s+)?(?:a|an|another|the|new)?\s*"
    r"(?:new\s+)?(?:[\w'’-]+\s+){0,3}?(?:event|appointment|meeting|reminder)\b"
)
_LEADING_FILLER = [
    r"(?:hey|hi|okay|ok|so|um|uh)\b[,.!]?\s*",
    r"(?:can|could|would|will)\s+you\s+",
    r"please\s+",
    r"(?:i\s+(?:need|want)\s+you\s+to|i'd\s+like\s+you\s+to|go\s+ahead\s+and|let's)\s+",
]


@dataclass(frozen=True)
class EventDraft:
    """An event ready to read back. `start`/`end` are None for an all-day event."""

    title: str
    day: dt.date
    start: dt.datetime | None = None
    end: dt.datetime | None = None

    @property
    def all_day(self) -> bool:
        return self.start is None


@dataclass(frozen=True)
class Problem:
    """A calendar request she couldn't turn into an event; `message` says why."""

    message: str


def strip_leading_filler(text: str, name: str | None = None) -> str:
    """Drop "hey Juniper, could you please ..." from the start."""
    patterns = list(_LEADING_FILLER)
    if name:
        patterns.insert(1, re.escape(name) + r"\b[,.!]?\s*")
    changed = True
    while changed:
        changed = False
        for pattern in patterns:
            new = re.sub("^" + pattern, "", text, flags=re.I)
            if new != text:
                text, changed = new, True
    return text


def is_calendar_request(text: str, name: str | None = None) -> bool:
    """True if the text asks to put something on the calendar.

    Questions ("what's on my calendar?", "did you add it to my calendar?") and
    negatives ("don't put that on my calendar") don't count.
    """
    text = strip_leading_filler(text.strip(), name)
    if len(text.split()) > 40 or _QUESTION_START.match(text) or _NEGATION.search(text):
        return False
    if re.match(r"(?:schedule|book|pencil\s+in)\b", text, re.I):
        return True
    if re.search(rf"\b{_VERB}\b.*\bcalendar\b", text, re.I):
        return True
    # "Create a test event for tomorrow at 3": no "calendar", but it names an event.
    return bool(re.match(_NEW_EVENT, text, re.I))


# ---- dates -------------------------------------------------------------------


def _cut(text: str, m: re.Match) -> str:
    return text[: m.start()] + " " + text[m.end() :]


def _next_weekday(today: dt.date, wd: int, mod: str | None) -> dt.date:
    if mod and mod.lower() == "next":
        # "next Friday" is Friday of next week (weeks start on Monday).
        monday = today - dt.timedelta(days=today.weekday()) + dt.timedelta(days=7)
        return monday + dt.timedelta(days=wd)
    return today + dt.timedelta(days=(wd - today.weekday()) % 7)


def _future_date(today: dt.date, month: int, day: int, year: int | None) -> dt.date | None:
    """The date with that month and day, this year or (if it's past) next year."""
    try:
        if year is not None:
            return dt.date(year if year > 99 else 2000 + year, month, day)
        d = dt.date(today.year, month, day)
        return d if d >= today else dt.date(today.year + 1, month, day)
    except ValueError:
        return None


@dataclass
class _Dates:
    rest: str  # the text with every date phrase cut out
    days: list[dt.date]
    weekdays: list[int]  # weekdays named, also next to an explicit date
    hint: str | None  # "am"/"pm" from "in the morning", "tonight", ...
    bad: str | None  # a date phrase that isn't a real date ("February 30")


def _find_dates(text: str, today: dt.date) -> _Dates:
    """Cut every date phrase out of `text` and resolve it against `today`."""
    days: list[dt.date] = []
    weekdays: list[int] = []
    hint: str | None = None
    bad: list[str] = []

    def part_hint(m: re.Match) -> None:
        nonlocal hint
        part = (m.groupdict().get("part") or "").lower()
        if part:
            hint = "am" if part == "morning" else "pm"

    patterns: list[tuple[str, Callable[[re.Match], dt.date | None]]] = []

    def explicit_month_day(m: re.Match) -> dt.date | None:
        year = int(m["year"]) if m["year"] else None
        return _future_date(today, MONTHS[m["month"][:3].lower()], int(m["day"]), year)

    patterns += [
        # "Thursday, November 12" / "November 12th, 2027"
        (
            rf"\b(?:on\s+)?(?:{_WEEKDAY},?\s+)?(?:the\s+)?{_MONTH}\s+(?:the\s+)?"
            rf"(?P<day>\d{{1,2}}){_ORD}(?:,?\s+(?P<year>\d{{4}}))?\b{_PART}",
            explicit_month_day,
        ),
        # "12 November" / "the 12th of November"
        (
            rf"\b(?:on\s+)?(?:{_WEEKDAY},?\s+)?(?:the\s+)?(?P<day>\d{{1,2}}){_ORD}\s+"
            rf"(?:of\s+)?{_MONTH}(?:,?\s+(?P<year>\d{{4}}))?\b{_PART}",
            explicit_month_day,
        ),
        # "11/12" or "11/12/2027" (month first)
        (
            rf"\b(?:on\s+)?(?:{_WEEKDAY},?\s+)?(?P<m>\d{{1,2}})/(?P<d>\d{{1,2}})"
            rf"(?:/(?P<y>\d{{2}}|\d{{4}}))?\b{_PART}",
            lambda m: _future_date(
                today, int(m["m"]), int(m["d"]), int(m["y"]) if m["y"] else None
            ),
        ),
        # "the 12th": this month, or next month if it has passed
        (
            rf"\b(?:on\s+)?(?:{_WEEKDAY},?\s+)?the\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)\b{_PART}",
            lambda m: _ordinal_day(today, int(m["day"])),
        ),
        (r"\b(?:on\s+)?the\s+day\s+after\s+tomorrow\b" + _PART, lambda m: today + dt.timedelta(2)),
        (r"\b(?:on\s+)?tomorrow\b" + _PART, lambda m: today + dt.timedelta(1)),
        (r"\b(?:on\s+)?today\b" + _PART, lambda m: today),
    ]
    for pattern, resolve in patterns:
        while m := re.search(pattern, text, re.I):
            day = resolve(m)
            if day is None:
                bad.append(m[0].strip())
            else:
                days.append(day)
                if m.groupdict().get("wd"):
                    weekdays.append(WEEKDAYS.index(m["wd"].lower()))
            part_hint(m)
            text = _cut(text, m)

    if m := re.search(r"\btonight\b", text, re.I):
        days.append(today)
        hint = "pm"
        text = _cut(text, m)

    weekday_re = rf"\b(?:on\s+)?(?:(?P<mod>this\s+coming|this|next|coming)\s+)?{_WEEKDAY}\b{_PART}"
    while m := re.search(weekday_re, text, re.I):
        wd = WEEKDAYS.index(m["wd"].lower())
        weekdays.append(wd)
        days.append(_next_weekday(today, wd, m["mod"]))
        part_hint(m)
        text = _cut(text, m)

    # Parts of the day on their own: "in the morning", "this evening", "at night".
    while m := re.search(
        r"\b(?:in\s+the|this)\s+(?P<part>morning|afternoon|evening)\b|\bat\s+(?P<n>night)\b",
        text,
        re.I,
    ):
        hint = "am" if (m["part"] or "").lower() == "morning" else "pm"
        if m[0].lower().startswith("this"):
            days.append(today)
        text = _cut(text, m)

    return _Dates(text, days, weekdays, hint, bad[0] if bad else None)


def _ordinal_day(today: dt.date, day: int) -> dt.date | None:
    for months_ahead in (0, 1, 2):
        month = (today.month - 1 + months_ahead) % 12 + 1
        year = today.year + (today.month - 1 + months_ahead) // 12
        try:
            d = dt.date(year, month, day)
        except ValueError:
            continue
        if d >= today:
            return d
    return None


# ---- times -------------------------------------------------------------------

_TIME = (
    r"(?:noon|midday|midnight|"
    r"\d{1,2}(?::[0-5]\d)?(?:\s*o'?clock)?(?:\s*(?:a\.?\s?m\.?|p\.?\s?m\.?))?)"
)
_END_OF_TIME = r"(?![\w:/])"


@dataclass(frozen=True)
class _Clock:
    hour: int
    minute: int
    meridiem: str | None  # "am", "pm" or None
    marked: bool  # had am/pm, a colon, o'clock, or is noon/midnight


def _clock(token: str) -> _Clock | None:
    t = token.lower().replace(" ", "").replace(".", "")
    if t in ("noon", "midday"):
        return _Clock(12, 0, "pm", True)
    if t == "midnight":
        return _Clock(0, 0, "am", True)
    m = re.fullmatch(r"(\d{1,2})(?::(\d\d))?(o'?clock)?(am|pm)?", t)
    if not m:
        return None
    hour, minute = int(m[1]), int(m[2] or 0)
    if hour > 23 or (m[4] and not 1 <= hour <= 12):
        return None
    return _Clock(hour, minute, m[4], bool(m[2] or m[3] or m[4]))


def _hour24(c: _Clock, meridiem: str | None) -> int:
    """24-hour clock hour, with `meridiem` standing in for a missing am/pm."""
    mer = c.meridiem or meridiem
    if c.hour >= 13 or (c.hour == 0 and c.meridiem is None):
        return c.hour
    if mer == "am":
        return 0 if c.hour == 12 else c.hour
    if mer == "pm":
        return c.hour if c.hour == 12 else c.hour + 12
    # No am/pm anywhere: assume business hours. 8-11 morning, 12-7 afternoon/evening.
    return c.hour + 12 if 1 <= c.hour <= 7 else c.hour


def _minutes(c: _Clock, meridiem: str | None) -> int:
    return _hour24(c, meridiem) * 60 + c.minute


def _range_minutes(a: _Clock, b: _Clock, hint: str | None) -> tuple[int, int]:
    """Start and end, in minutes after midnight, for "a to b"."""
    if b.meridiem and not a.meridiem:
        # "2 to 4pm" is 2pm; "11 to 1pm" is 11am.
        end = _minutes(b, None)
        start = _minutes(a, b.meridiem)
        if start >= end:
            start = _minutes(a, "am" if b.meridiem == "pm" else "pm")
        return start, end
    start = _minutes(a, hint)
    if b.meridiem:
        return start, _minutes(b, None)
    # The end is the first matching clock time after the start: "9 to 5" ends at 5pm.
    options = [b.hour * 60 + b.minute]
    if b.hour < 12:
        options.append((b.hour + 12) * 60 + b.minute)
    if b.hour == 12:
        options = [12 * 60 + b.minute, 24 * 60 + b.minute]
    later = [o for o in options if o > start]
    return start, min(later) if later else options[0]


_DURATION = re.compile(
    r"\bfor\s+(?:(?P<half_an>half\s+an?\s+hour)|"
    r"(?P<n>an?|one|two|three|four|five|six|\d+(?:\.\d+)?)\s+"
    r"(?P<unit>hours?|hrs?|minutes?|mins?)(?P<and_half>\s+and\s+a\s+half)?)\b",
    re.I,
)
_NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


def _duration_minutes(m: re.Match) -> int:
    if m["half_an"]:
        return 30
    raw = m["n"].lower()
    n = float(_NUMBER_WORDS.get(raw, raw))  # type: ignore[arg-type]
    if m["and_half"]:
        n += 0.5
    return round(n * 60) if m["unit"].lower().startswith("h") else round(n)


# ---- title -------------------------------------------------------------------

_CALENDAR_PHRASE = re.compile(
    r"\b(?:to|on|in|onto|into|for)\s+(?:my|the|our)\s+(?:google\s+)?calendar\b"
    r"|\b(?:my|the)\s+calendar\b",
    re.I,
)
_EDGE_WORDS = (
    r"on|at|for|from|to|and|in|by|this|next|of|it's|it\s+is|which\s+is|that's|"
    r"please|thanks|thank\s+you|for\s+me|called|named|titled"
)


def _trim_edges(text: str) -> str:
    """Strip filler words and punctuation from both ends ("on", "at", "please", ...)."""
    edge = re.compile(
        rf"^(?:[\s,;:.!-]|(?:{_EDGE_WORDS})\b)+|(?:[\s,;:.!?-]|\b(?:{_EDGE_WORDS}))+$", re.I
    )
    prev = None
    while prev != text:
        prev = text
        text = edge.sub("", text).strip()
        text = re.sub(r"\s+in$", "", text, flags=re.I)  # "pencil lunch in"
    return text


def _title(rest: str, name: str | None) -> str:
    text = _CALENDAR_PHRASE.sub(" ", rest)
    text = strip_leading_filler(re.sub(r"\s+", " ", text).strip(), name)
    text = re.sub(
        rf"^{_VERB}(?:\s+in)?\b\s*(?:(?:a|an)\s+)?(?:new\s+)?"
        r"(?:(?:calendar\s+)?(?:event|entry)\s+(?:called|named|titled|for)\s+)?",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(r"^(?:a|an|the)\s+", "", text, flags=re.I)
    text = _trim_edges(text)
    text = re.sub(r"\s+([,.])", r"\1", re.sub(r"\s+", " ", text))
    return text[:1].upper() + text[1:]


# ---- putting it together -----------------------------------------------------


def parse_event(
    text: str,
    now: dt.datetime,
    *,
    default_minutes: int = 60,
    name: str | None = None,
) -> EventDraft | Problem | None:
    """An `EventDraft` for a calendar request, a `Problem` if it's a calendar
    request she can't turn into an event, or None if it isn't one at all.

    `now` is naive local time.
    """
    if not is_calendar_request(text, name):
        return None
    today = now.date()
    found = _find_dates(text, today)
    if found.bad:
        return Problem(f"I don't think {found.bad} is a real date. Which day is it?")
    rest, hint, weekdays = found.rest, found.hint, found.weekdays

    duration = None
    if m := _DURATION.search(rest):
        duration = _duration_minutes(m)
        rest = _cut(rest, m)

    start_min = end_min = None
    range_re = (
        rf"(?:\bfrom\s+)?\b(?P<a>{_TIME}){_END_OF_TIME}\s*"
        rf"(?:-|–|—|\bto\b|\buntil\b|\btill\b|\bthrough\b)\s*(?P<b>{_TIME}){_END_OF_TIME}"
    )
    for m in re.finditer(range_re, rest, re.I):
        a, b = _clock(m["a"]), _clock(m["b"])
        if a is None or b is None:
            continue
        said_from = m[0].lower().startswith("from")
        if said_from or a.marked or b.marked or (a.hour <= 12 and b.hour <= 12):
            start_min, end_min = _range_minutes(a, b, hint)
            rest = _cut(rest, m)
            break
    if start_min is None:
        single_re = rf"(?P<at>\b(?:at|@)\s*|@\s*)?\b(?P<t>{_TIME}){_END_OF_TIME}"
        for m in re.finditer(single_re, rest, re.I):
            c = _clock(m["t"])
            if c is not None and (m["at"] or c.marked):
                start_min = _minutes(c, hint)
                rest = _cut(rest, m)
                break

    if not re.search(r"\bcalendar\b", text, re.I) and not found.days and start_min is None:
        return None  # "create a meeting agenda": no calendar, no when, so not a request
    title = _title(rest, name)
    if not title:
        return Problem("What should I call it?")

    distinct = sorted(set(found.days))
    if len(distinct) > 1:
        return Problem(
            f"I heard two different days, {spoken_date(distinct[0], today)} and "
            f"{spoken_date(distinct[1], today)}. Which one is it?"
        )
    day = distinct[0] if distinct else None
    if day is not None and weekdays and day.weekday() not in weekdays:
        said = WEEKDAYS[weekdays[0]].capitalize()
        return Problem(f"{day:%B} {day.day} is a {day:%A}, not a {said}. Which day did you mean?")
    if day is None and re.search(r"\b(?:next\s+week|weekend)\b", text, re.I):
        return Problem("Which day is that?")

    if start_min is None:
        if duration is not None:
            return Problem("What time does it start?")
        if day is None:
            return Problem("When is it?")
        return EventDraft(title, day)

    if day is None:  # a time but no day: the next time that clock time comes round
        candidate = dt.datetime.combine(today, dt.time()) + dt.timedelta(minutes=start_min)
        day = today if candidate > now else today + dt.timedelta(days=1)
    midnight = dt.datetime.combine(day, dt.time())
    start = midnight + dt.timedelta(minutes=start_min)
    if end_min is not None:
        if end_min <= start_min and end_min < 12 * 60 <= start_min:
            end_min += 24 * 60  # "10pm to 2am" ends the next morning
        if end_min <= start_min:
            return Problem("The end time is before the start time. When does it end?")
        end = midnight + dt.timedelta(minutes=end_min)
    else:
        end = start + dt.timedelta(minutes=duration or default_minutes)
    return EventDraft(title, day, start, end)


# ---- removing ------------------------------------------------------------------

_REMOVE_VERB = r"(?:remove|delete|cancel|clear|scrap|drop|take|get\s+rid\s+of)"
_REMOVE_CALENDAR = re.compile(
    r"\b(?:off|from|on|in|out\s+of)\s+(?:of\s+)?(?:my|the|our)\s+(?:google\s+)?calendar\b"
    r"|\b(?:my|the|our)\s+(?:google\s+)?calendar\b",
    re.I,
)


@dataclass(frozen=True)
class RemoveRequest:
    """Which of her events to remove: words from the title, and optionally the day.
    An empty title means "any event of hers on that day"."""

    title: str
    day: dt.date | None = None


def is_removal_request(text: str, name: str | None = None) -> bool:
    """True for "remove X from my calendar" and friends. Needs the word
    "calendar", so "cancel that" or "delete it" never start one."""
    text = strip_leading_filler(text.strip(), name)
    if len(text.split()) > 30 or _QUESTION_START.match(text) or _NEGATION.search(text):
        return False
    return bool(re.match(rf"{_REMOVE_VERB}\b.*\bcalendar\b", text, re.I))


def parse_removal(
    text: str, now: dt.datetime, *, name: str | None = None
) -> RemoveRequest | Problem | None:
    """A `RemoveRequest`, a `Problem` if it's unclear, or None if it isn't one."""
    if not is_removal_request(text, name):
        return None
    today = now.date()
    found = _find_dates(text, today)
    if found.bad:
        return Problem(f"I don't think {found.bad} is a real date. Which day is it?")
    distinct = sorted(set(found.days))
    if len(distinct) > 1:
        return Problem(
            f"I heard two different days, {spoken_date(distinct[0], today)} and "
            f"{spoken_date(distinct[1], today)}. Which one is it?"
        )
    day = distinct[0] if distinct else None
    if day is not None and found.weekdays and day.weekday() not in found.weekdays:
        said = WEEKDAYS[found.weekdays[0]].capitalize()
        return Problem(f"{day:%B} {day.day} is a {day:%A}, not a {said}. Which day did you mean?")
    rest = _REMOVE_CALENDAR.sub(" ", found.rest)
    rest = strip_leading_filler(re.sub(r"\s+", " ", rest).strip(), name)
    rest = re.sub(rf"^{_REMOVE_VERB}\s+", "", rest, flags=re.I)
    title = _trim_edges(re.sub(r"^(?:a|an|the|my|that|this)\s+", "", rest, flags=re.I))
    title = re.sub(r"\s+(?:events?|entry|entries)$", "", title, flags=re.I)
    title = re.sub(
        r"^(?:events?|entry|it|that|this|them|everything|all|all\s+events)$", "", title, flags=re.I
    )
    if not title and day is None:
        return Problem("Which event should I remove?")
    return RemoveRequest(title.strip(), day)


# ---- reading it back ---------------------------------------------------------


def spoken_date(day: dt.date, today: dt.date) -> str:
    """'Thursday, November 5' (plus the year when it isn't this year)."""
    text = f"{day:%A, %B} {day.day}"  # "%-d" isn't portable to Windows
    return text if day.year == today.year else f"{text}, {day.year}"


def spoken_time(t: dt.datetime, *, meridiem: bool = True) -> str:
    if t.hour == 12 and t.minute == 0:
        return "noon"
    if t.hour == 0 and t.minute == 0:
        return "midnight"
    hour = t.hour % 12 or 12
    text = f"{hour}:{t.minute:02d}" if t.minute else str(hour)
    return f"{text} {'AM' if t.hour < 12 else 'PM'}" if meridiem else text


def describe_event(event: EventDraft, today: dt.date) -> str:
    """'Dentist, Thursday, November 5, 3 to 4 PM' for the read-back."""
    when = spoken_date(event.day, today)
    if event.all_day:
        return f"{event.title}, {when}, all day"
    assert event.start is not None and event.end is not None
    same_half = (event.start.hour < 12) == (event.end.hour < 12)
    plain_start = same_half and spoken_time(event.start) not in ("noon", "midnight")
    start = spoken_time(event.start, meridiem=not plain_start)
    end = spoken_time(event.end)
    if event.end.date() != event.day:
        end += f" on {event.end:%A}"
    return f"{event.title}, {when}, {start} to {end}"


# ---- yes / no ------------------------------------------------------------------

_YES = re.compile(
    r"^(?:yes|yeah|yep|yup|ya|sure|ok|okay|do\s+it|go\s+ahead|please\s+do|add\s+it|"
    r"confirm(?:ed)?|correct|that's\s+right|sounds\s+good|perfect|yes\s+please)\b",
    re.I,
)
_NO = re.compile(
    r"^(?:no|nope|nah|cancel|don't|do\s+not|never\s*mind|forget\s+it|stop|wait|hold\s+on)\b",
    re.I,
)


def answer_to_confirm(text: str, name: str | None = None) -> bool | None:
    """True for yes, False for no, None if the text is neither (a new request)."""
    # "Okay" is filler at the start of a request but a yes on its own, so
    # only a leading "hey Juniper," is dropped here.
    if name:
        text = re.sub(rf"^\s*(?:hey\s+)?{re.escape(name)}\b[,.!]?\s*", "", text, flags=re.I)
    text = text.strip()
    if _NO.match(text):
        return False
    if _YES.match(text) and len(text.split()) <= 6:
        return True
    return None
