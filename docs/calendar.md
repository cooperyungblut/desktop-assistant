# Calendar: adding events by voice or chat

Say or type something like:

- "Add lunch with Sam to my calendar tomorrow at noon"
- "Schedule a call with the recruiter November 12 at 10:30 for 30 minutes"
- "Put the career fair on my calendar for the 20th from 9 until 5"

She reads the event back ("Lunch with Sam, Thursday, November 5, noon to
1 PM. Should I add it to your calendar?") and adds it only after a yes.
"No" (or "wait", "cancel") drops it; anything else drops it and is answered
as a normal message. If she can't tell the day or the name, she asks
instead of guessing ("November 12 is a Thursday, not a Wednesday. Which day
did you mean?").

## Removing events

"Remove the test event from my calendar", "delete lunch with Sam from my
calendar on Friday" or "clear my calendar for Friday" (every event she added
that day). She finds the event, reads it back ("Test event, Thursday,
November 5, 3 to 4 PM. Should I remove it from your calendar?") and deletes
it only after a yes. If more than one event matches she lists them and asks
you to say the day; she never deletes several at once.

She can only remove events she added herself. Each one she creates carries a
hidden marker, and she only searches for events with it, so your own events
can't be touched. Events added before the marker existed (including the test
events from before this was added) don't have it; delete those in Google
Calendar. Two events with the same name on the same day can't be told apart
by voice, so she'll ask for a different day; delete one of them by hand.

## How it works

- The request is parsed in code (`secretary/calendar_requests.py`), not by
  the local model, for the same reason the brief is read verbatim: a 12B
  model doing date arithmetic is the wrong place for a wrong answer. The
  parser cuts the date, time and duration phrases out of the sentence,
  resolves them against today's date, and the rest is the title.
- A time without am/pm is read as business hours (8 to 11 morning, 12 to 7
  afternoon); "in the morning" or "tonight" overrides that. No time makes
  an all-day event. "Friday" is the coming Friday, "next Friday" the one in
  next week.
- Only text you typed or said can start a calendar request. Brief content
  never does, and the model can't create or remove events at all.
- Events go to Google Calendar through its REST API (`google_calendar.py`)
  with the `calendar.events` scope only. The Calendar API is free for
  personal use.

## Setup (once, about 10 minutes)

1. Go to [console.cloud.google.com](https://console.cloud.google.com/) and
   create a project (any name, for example `desk-secretary`).
2. **APIs & Services → Library**: search for **Google Calendar API** and
   click **Enable**.
3. **Google Auth Platform** (or **OAuth consent screen**): set an app name
   and your email, choose **External** as the audience, and add your own
   Google account as a test user.
4. Still under **Audience**, click **Publish app** to move it to
   **In production**. Apps left in "Testing" get sign-ins that expire after
   7 days. A personal app doesn't need Google's verification; you'll just
   see an "unverified app" screen when you sign in, which is expected for
   an app you made yourself.
5. **Clients → Create client**: application type **Desktop app**. Download
   the JSON and save it as `data/google-oauth-client.json` in the repo
   folder. (`data/` is git-ignored. Don't share this file.)
6. In `config.yaml`:

   ```yaml
   calendar:
     engine: google
   ```

7. Sign in:

   ```powershell
   uv run python scripts/connect_calendar.py
   ```

   Your browser opens Google's sign-in. On the "unverified app" screen click
   **Advanced → Go to (your app name)**, then allow access to calendar
   events. The script prints `Connected: she can add events to the calendar
   '...'` and saves the sign-in to `data/google-token.json`.
8. Restart the app and try "add a test event to my calendar tomorrow at 3".

`uv run python scripts/connect_calendar.py --check` tests the saved sign-in
later. If she says the sign-in expired, run the script again without
`--check`. To disconnect, delete `data/google-token.json` and remove the
app at [myaccount.google.com/permissions](https://myaccount.google.com/permissions).

## Settings

```yaml
calendar:
  engine: google        # or none
  calendar_id: primary  # or a calendar's ID from Google Calendar settings
  default_minutes: 60   # event length when you only say a start time
  confirm_seconds: 300  # how long "Should I add it?" waits for a yes
```

Her lines (`calendar_confirm`, `calendar_added`, `calendar_cancelled`,
`calendar_failed`, `calendar_off`, and the `calendar_remove_*` ones) can be changed under `lines:` in the
persona file.
