"""Add events to Google Calendar through its REST API.

Plain httpx again, like the Ollama client: the API is two endpoints (OAuth
token, events.insert), so a client library would hide more than it saves.

Sign-in is Google's flow for desktop apps: `scripts/connect_calendar.py`
opens the consent page in the browser, Google redirects back to a one-shot
server on 127.0.0.1, and the refresh token is saved to `calendar.token_file`
(under data/, which is git-ignored). PKCE protects the code exchange. The
app only asks for the `calendar.events` scope: it can add and read events,
not change calendar settings or sharing.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import http.server
import json
import logging
import os
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from .calendar_requests import AgendaRequest, EventDraft, RemoveRequest
from .config import CalendarConfig

log = logging.getLogger(__name__)

SCOPE = "https://www.googleapis.com/auth/calendar.events"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API_URL = "https://www.googleapis.com/calendar/v3"


# Every event she adds carries this private property, and she only ever finds
# or removes events that have it. Your own events are out of her reach.
MARKER = "deskSecretary"


@dataclass(frozen=True)
class FoundEvent:
    id: str
    draft: EventDraft  # in the PC's local time, for the read-back


class CalendarError(RuntimeError):
    """Adding the event failed. The message is short and safe to say out loud
    ("Google Calendar didn't answer"); details go to the log."""


class Calendar(Protocol):
    @property
    def available(self) -> bool: ...

    async def add(self, event: EventDraft, *, added_by: str | None = None) -> str:
        """Create the event; returns a link to it. `added_by` (the persona's name)
        goes in the event's description."""
        ...

    async def find(self, request: RemoveRequest, now: dt.datetime) -> list[FoundEvent]:
        """Her events (added by her, still upcoming) that match the request."""
        ...

    async def remove(self, event: FoundEvent) -> None: ...

    async def agenda(self, request: AgendaRequest) -> list[EventDraft]:
        """Everything on that day of the calendar, yours and hers, in time order.
        Read-only."""
        ...

    async def aclose(self) -> None: ...


class NoCalendar:
    """calendar.engine: none, or not signed in yet."""

    available = False

    async def add(self, event: EventDraft, *, added_by: str | None = None) -> str:
        raise CalendarError("the calendar isn't connected yet")

    async def find(self, request: RemoveRequest, now: dt.datetime) -> list[FoundEvent]:
        raise CalendarError("the calendar isn't connected yet")

    async def remove(self, event: FoundEvent) -> None:
        raise CalendarError("the calendar isn't connected yet")

    async def agenda(self, request: AgendaRequest) -> list[EventDraft]:
        raise CalendarError("the calendar isn't connected yet")

    async def aclose(self) -> None:
        pass


def event_body(event: EventDraft, added_by: str | None = None) -> dict[str, Any]:
    """The events.insert body. Times carry the PC's UTC offset for that date."""
    if event.all_day:
        start: dict[str, str] = {"date": event.day.isoformat()}
        end = {"date": (event.day + dt.timedelta(days=1)).isoformat()}  # end is exclusive
    else:
        assert event.start is not None and event.end is not None
        # A naive datetime's astimezone() reads it as local time, with the
        # daylight-saving rules for that date, not today's.
        start = {"dateTime": event.start.astimezone().isoformat()}
        end = {"dateTime": event.end.astimezone().isoformat()}
    body: dict[str, Any] = {
        "summary": event.title,
        "start": start,
        "end": end,
        "extendedProperties": {"private": {MARKER: "1"}},
    }
    if added_by:
        body["description"] = f"Added by {added_by}."
    return body


# ---- tokens ------------------------------------------------------------------


def load_client(path: Path) -> tuple[str, str]:
    """client_id and client_secret from the JSON downloaded from Google Cloud."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CalendarError(f"can't read the OAuth client file {path}: {exc}") from exc
    info = raw.get("installed") or raw.get("web") or raw
    try:
        return info["client_id"], info["client_secret"]
    except KeyError as exc:
        raise CalendarError(f"{path.name} has no {exc.args[0]}; download it again") from exc


def save_token(path: Path, token: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    # Owner-only on Linux/macOS. On Windows the file inherits the folder's
    # permissions, which for a folder in your user profile means only you.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(token, f, indent=2)
    os.replace(tmp, path)


def _with_expiry(token: dict[str, Any], now: float) -> dict[str, Any]:
    token = dict(token)
    token["expires_at"] = now + float(token.pop("expires_in", 3600))
    return token


class GoogleCalendar:
    def __init__(
        self,
        config: CalendarConfig,
        client: httpx.AsyncClient | None = None,
        clock=time.time,
    ):
        self._config = config
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))
        self._clock = clock
        self._token: dict[str, Any] | None = None

    @property
    def available(self) -> bool:
        return self._config.token_file.is_file() and self._config.client_file.is_file()

    async def add(self, event: EventDraft, *, added_by: str | None = None) -> str:
        cal = urllib.parse.quote(self._config.calendar_id, safe="")
        body = event_body(event, added_by)
        r = await self._request("POST", f"{API_URL}/calendars/{cal}/events", json=body)
        data = r.json()
        log.info("Added calendar event %s", data.get("id"))
        return data.get("htmlLink", "")

    async def find(self, request: RemoveRequest, now: dt.datetime) -> list[FoundEvent]:
        """Upcoming events she added whose title contains the request's words,
        on the request's day if it has one."""
        if request.day is not None:
            first = dt.datetime.combine(request.day, dt.time())
            last = first + dt.timedelta(days=1)
        else:
            first, last = now, now + dt.timedelta(days=365)
        params = {
            "privateExtendedProperty": f"{MARKER}=1",
            "timeMin": first.astimezone().isoformat(),
            "timeMax": last.astimezone().isoformat(),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": "50",
        }
        r = await self._request("GET", f"{self._events_url()}", params=params)
        wanted = request.title.lower()
        found = []
        for item in r.json().get("items", []):
            title = item.get("summary", "")
            draft = _draft_from_item(item)
            if item.get("id") and draft and wanted in title.lower():
                found.append(FoundEvent(item["id"], draft))
        return found

    async def agenda(self, request: AgendaRequest) -> list[EventDraft]:
        first = dt.datetime.combine(request.day, dt.time())
        params = {
            "timeMin": first.astimezone().isoformat(),
            "timeMax": (first + dt.timedelta(days=1)).astimezone().isoformat(),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": "50",
        }
        r = await self._request("GET", self._events_url(), params=params)
        drafts = []
        for item in r.json().get("items", []):
            if item.get("status") == "cancelled":
                continue
            draft = _draft_from_item(item)
            if draft is not None:
                drafts.append(draft)
        return drafts

    async def remove(self, event: FoundEvent) -> None:
        await self._request(
            "DELETE", f"{self._events_url()}/{urllib.parse.quote(event.id, safe='')}"
        )
        log.info("Removed calendar event %s", event.id)

    def _events_url(self) -> str:
        cal = urllib.parse.quote(self._config.calendar_id, safe="")
        return f"{API_URL}/calendars/{cal}/events"

    async def calendar_name(self) -> str:
        """The calendar's name, read with one events.list call. Proves the saved
        sign-in still refreshes and the scope allows events."""
        cal = urllib.parse.quote(self._config.calendar_id, safe="")
        url = f"{API_URL}/calendars/{cal}/events"
        r = await self._request("GET", url, params={"maxResults": 1})
        return str(r.json().get("summary", self._config.calendar_id))

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        for attempt in (1, 2):
            headers = {"Authorization": f"Bearer {await self._access_token()}"}
            try:
                r = await self._client.request(method, url, headers=headers, **kwargs)
            except httpx.HTTPError as exc:
                log.warning("Google Calendar request failed: %s", exc)
                raise CalendarError("Google Calendar didn't answer") from exc
            if r.status_code == 401 and attempt == 1:
                self._loaded()["access_token"] = ""  # refresh once, then give up
                continue
            if r.status_code >= 400:
                log.warning("Google Calendar said %s: %s", r.status_code, _error_text(r))
                raise CalendarError(f"Google Calendar returned an error ({r.status_code})")
            return r
        raise CalendarError("Google Calendar turned down the sign-in, so it needs reconnecting")

    def _loaded(self) -> dict[str, Any]:
        if self._token is None:
            try:
                self._token = json.loads(self._config.token_file.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise CalendarError("the calendar isn't connected yet") from exc
        return self._token

    async def _access_token(self) -> str:
        token = self._loaded()
        if token.get("access_token") and token.get("expires_at", 0) > self._clock() + 60:
            return token["access_token"]
        if not token.get("refresh_token"):
            raise CalendarError("the calendar sign-in is incomplete, so it needs reconnecting")
        client_id, secret = load_client(self._config.client_file)
        try:
            r = await self._client.post(
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": token["refresh_token"],
                    "client_id": client_id,
                    "client_secret": secret,
                },
            )
        except httpx.HTTPError as exc:
            log.warning("Google sign-in refresh failed: %s", exc)
            raise CalendarError("Google didn't answer") from exc
        if r.status_code != 200:
            # invalid_grant: revoked, or the 7-day limit on apps left in "Testing".
            log.warning(
                "Google refused the refresh token (%s). Run scripts/connect_calendar.py again.",
                _error_text(r),
            )
            raise CalendarError("the calendar sign-in has expired, so it needs reconnecting")
        fresh = _with_expiry(r.json(), self._clock())
        fresh.setdefault("refresh_token", token["refresh_token"])
        self._token = fresh
        save_token(self._config.token_file, fresh)
        return fresh["access_token"]

    async def aclose(self) -> None:
        await self._client.aclose()


def _draft_from_item(item: dict[str, Any]) -> EventDraft | None:
    """An events.list item as a local-time EventDraft (None if it has no start)."""
    title = item.get("summary", "(no title)")
    start, end = item.get("start", {}), item.get("end", {})
    try:
        if "dateTime" in start:
            begins = dt.datetime.fromisoformat(start["dateTime"]).astimezone().replace(tzinfo=None)
            ends = dt.datetime.fromisoformat(end["dateTime"]).astimezone().replace(tzinfo=None)
            return EventDraft(title, begins.date(), begins, ends)
        if "date" in start:
            return EventDraft(title, dt.date.fromisoformat(start["date"]))
    except (KeyError, ValueError):
        pass
    return None


def _error_text(r: httpx.Response) -> str:
    try:
        return str(r.json()["error"]["message"])[:200]
    except (ValueError, KeyError, TypeError):
        return r.text[:200]


def make_calendar(config: CalendarConfig) -> Calendar:
    if config.engine == "none":
        return NoCalendar()
    if config.engine != "google":
        raise ValueError(f"unknown calendar.engine {config.engine!r}; use google or none")
    cal = GoogleCalendar(config)
    if not cal.available:
        log.warning(
            "Calendar not connected yet (%s missing). "
            "Run: uv run python scripts/connect_calendar.py",
            config.token_file if config.client_file.is_file() else config.client_file,
        )
    return cal


# ---- one-time sign-in (run from scripts/connect_calendar.py) -----------------


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def auth_url(client_id: str, redirect_uri: str, state: str, challenge: str) -> str:
    query = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",  # ask for a refresh token
        "prompt": "consent",  # ...every time, so reconnecting always gets a new one
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(query)}"


def wait_for_code(server: http.server.HTTPServer, state: str, timeout: float) -> str:
    """Serve the one redirect from Google and return its code."""
    result: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (stdlib name)
            params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
            if params.get("state") != state:
                self.send_error(400, "Unexpected request")
                return
            result.update(params)
            ok = "code" in params
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            message = (
                "Calendar connected. You can close this tab."
                if ok
                else f"Sign-in failed: {params.get('error', 'unknown error')}"
            )
            self.wfile.write(f"<p>{message}</p>".encode())

        def log_message(self, *args: Any) -> None:
            pass

    server.RequestHandlerClass = Handler
    server.timeout = 1.0
    deadline = time.monotonic() + timeout
    while not result and time.monotonic() < deadline:
        server.handle_request()
    if "code" not in result:
        raise CalendarError(result.get("error", "timed out waiting for the Google sign-in"))
    return result["code"]


def authorize(config: CalendarConfig, open_browser, timeout: float = 300.0) -> None:
    """Run the browser sign-in once and save the refresh token."""
    client_id, secret = load_client(config.client_file)
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(16)
    server = http.server.HTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
    redirect_uri = f"http://127.0.0.1:{server.server_address[1]}/"
    try:
        url = auth_url(client_id, redirect_uri, state, challenge)
        threading.Thread(target=open_browser, args=(url,), daemon=True).start()
        code = wait_for_code(server, state, timeout)
    finally:
        server.server_close()
    r = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": secret,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        },
        timeout=15.0,
    )
    if r.status_code != 200:
        raise CalendarError(f"Google refused the sign-in code: {_error_text(r)}")
    token = _with_expiry(r.json(), time.time())
    if not token.get("refresh_token"):
        raise CalendarError("Google sent no refresh token; remove the app's access and retry")
    save_token(config.token_file, token)
