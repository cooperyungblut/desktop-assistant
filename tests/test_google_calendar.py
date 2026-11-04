import datetime as dt
import json

import httpx
import pytest

from secretary.calendar_requests import EventDraft
from secretary.config import CalendarConfig
from secretary.google_calendar import (
    TOKEN_URL,
    CalendarError,
    GoogleCalendar,
    NoCalendar,
    auth_url,
    event_body,
    make_calendar,
)

DAY = dt.date(2026, 11, 5)


@pytest.fixture
def config(tmp_path):
    client = tmp_path / "client.json"
    client.write_text(json.dumps({"installed": {"client_id": "cid", "client_secret": "sec"}}))
    token = tmp_path / "token.json"
    token.write_text(json.dumps({"access_token": "old", "refresh_token": "r1", "expires_at": 0}))
    return CalendarConfig(engine="google", client_file=client, token_file=token)


class FakeGoogle:
    """Records requests; answers the token and events endpoints."""

    def __init__(self, token_status=200, event_status=200):
        self.requests: list[httpx.Request] = []
        self.token_status = token_status
        self.event_status = event_status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if str(request.url) == TOKEN_URL:
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": "new", "expires_in": 3599})
        if self.event_status != 200:
            return httpx.Response(self.event_status, json={"error": {"message": "nope"}})
        return httpx.Response(200, json={"id": "e1", "htmlLink": "https://calendar/e1"})


def calendar_with(config, google):
    client = httpx.AsyncClient(transport=httpx.MockTransport(google))
    return GoogleCalendar(config, client=client, clock=lambda: 1000.0)


def test_timed_event_body_has_local_offset():
    start = dt.datetime(2026, 11, 5, 15, 0)
    body = event_body(EventDraft("Dentist", DAY, start, start + dt.timedelta(hours=1)))
    assert body["summary"] == "Dentist"
    begins = dt.datetime.fromisoformat(body["start"]["dateTime"])
    assert begins.tzinfo is not None and begins.replace(tzinfo=None) == start
    assert body["end"]["dateTime"].startswith("2026-11-05T16:00:00")


def test_all_day_event_ends_the_next_day():
    body = event_body(EventDraft("Birthday", DAY))
    assert body["start"] == {"date": "2026-11-05"}
    assert body["end"] == {"date": "2026-11-06"}  # Google's end date is exclusive


async def test_add_refreshes_the_token_then_creates(config):
    google = FakeGoogle()
    cal = calendar_with(config, google)
    assert cal.available
    link = await cal.add(EventDraft("Birthday", DAY))
    assert link == "https://calendar/e1"

    refresh, create = google.requests
    assert b"grant_type=refresh_token" in refresh.content and b"refresh_token=r1" in refresh.content
    assert create.url.path == "/calendar/v3/calendars/primary/events"
    assert create.headers["Authorization"] == "Bearer new"
    assert json.loads(create.content)["summary"] == "Birthday"
    saved = json.loads(config.token_file.read_text())
    assert saved["access_token"] == "new" and saved["refresh_token"] == "r1"
    assert saved["expires_at"] == 1000.0 + 3599

    await cal.add(EventDraft("Again", DAY))  # token still fresh: no second refresh
    assert len(google.requests) == 3


async def test_expired_sign_in_says_so(config):
    cal = calendar_with(config, FakeGoogle(token_status=400))
    with pytest.raises(CalendarError, match="expired"):
        await cal.add(EventDraft("Birthday", DAY))


async def test_api_error_is_short(config):
    cal = calendar_with(config, FakeGoogle(event_status=403))
    with pytest.raises(CalendarError) as err:
        await cal.add(EventDraft("Birthday", DAY))
    assert str(err.value) == "Google Calendar returned an error (403)"


def test_not_signed_in_is_unavailable(config):
    config.token_file.unlink()
    assert not make_calendar(config).available
    assert isinstance(make_calendar(CalendarConfig()), NoCalendar)


def test_auth_url_asks_only_for_events_with_pkce():
    url = httpx.URL(auth_url("cid", "http://127.0.0.1:5000/", "st", "chal"))
    q = url.params
    assert q["scope"] == "https://www.googleapis.com/auth/calendar.events"
    assert q["code_challenge_method"] == "S256" and q["code_challenge"] == "chal"
    assert q["access_type"] == "offline" and q["state"] == "st"


def test_sign_in_redirect_is_caught_and_state_checked(config, monkeypatch):
    import urllib.request

    from secretary import google_calendar

    def fake_browser(url):
        q = httpx.URL(url).params
        back = q["redirect_uri"]
        with pytest.raises(urllib.error.HTTPError):  # wrong state is refused
            urllib.request.urlopen(f"{back}?code=evil&state=other")
        urllib.request.urlopen(f"{back}?code=good&state={q['state']}").read()

    sent = {}

    def fake_post(url, data, timeout):
        sent.update(data)
        return httpx.Response(
            200, json={"access_token": "a", "refresh_token": "r", "expires_in": 60}
        )

    monkeypatch.setattr(google_calendar.httpx, "post", fake_post)
    google_calendar.authorize(config, fake_browser, timeout=10)
    assert sent["code"] == "good" and sent["grant_type"] == "authorization_code"
    assert len(sent["code_verifier"]) >= 43
    assert json.loads(config.token_file.read_text())["refresh_token"] == "r"
