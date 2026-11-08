"""The assistant's behaviour, independent of the web transport.

`Assistant` owns the current brief, the conversation history and the
pipelines that turn text into speech. It reports everything it does through
an `emit` callback, so the WebSocket server just forwards events and tests
can collect them in a list.

Event types sent to the UI:
    briefs       {briefs: [{id, type, date, handled}], ready: id | None}
    status       {model: bool, tts: bool, stt: bool, persona: name}
    transcript   {text}         what push-to-talk heard ("" if nothing)
    readout      {brief: {id, type, date, display}}
    say          {seq, text, audio: base64 WAV | None}     one sentence of speech
    expression   {mood, faces, talk_faces}   the avatar's face for what she says
    speech_done  {}             all speech for the current action has been sent
    reply_start  {id}
    reply_token  {id, text}
    reply_end    {id, text}
    escalated    {id, question}
    calendar_added {title, link}  an event she added after a yes
    calendar_removed {title}      an event she removed after a yes
    note_added   {page, text}     a note she wrote down
    error        {message}
"""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import logging
import random
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from . import commands, escalations
from .briefs import Brief
from .calendar_requests import (
    EventDraft,
    Problem,
    answer_to_confirm,
    day_phrase,
    describe_agenda_item,
    describe_event,
    parse_agenda,
    parse_event,
    parse_removal,
)
from .config import CalendarConfig, FramingConfig
from .expressions import TagFilter, strip_tags
from .framing import BriefFramer, FrameLine, paragraphs, split_greeting, split_signoff
from .google_calendar import Calendar, CalendarError, FoundEvent, NoCalendar
from .llm import ChatModel, LlmUnavailable, SentenceSplitter, split_sentences
from .lookup import Lookup, follow_up, lookup_request, render, usable
from .notes import Notebook, NoteRequest, clean_note, is_cancel, note_request
from .persona import Persona
from .prompt import Turn, build_messages
from .state import StateStore
from .stt import NoTranscriber, SttUnavailable, Transcriber
from .tts import Speaker
from .vocabulary import speech_hints
from .watcher import BriefScanner

log = logging.getLogger(__name__)
Emit = Callable[[dict[str, Any]], Awaitable[None]]


class Assistant:
    def __init__(
        self,
        *,
        persona: Persona,
        model: ChatModel,
        speaker: Speaker,
        scanner: BriefScanner,
        state: StateStore,
        escalations_dir: Path,
        emit: Emit,
        transcriber: Transcriber | None = None,
        history_turns: int = 12,
        framing: FramingConfig | None = None,
        calendar: Calendar | None = None,
        calendar_config: CalendarConfig | None = None,
        wiki: Lookup | None = None,
        notebook: Notebook | None = None,
        wiki_timeout: float = 15.0,
        wiki_notice_after: float = 1.5,
        wiki_mention_source: bool = True,
        now: Callable[[], dt.datetime] = dt.datetime.now,
    ):
        self.persona = persona
        self._model = model
        self._speaker = speaker
        self._scanner = scanner
        self._state = state
        self._escalations_dir = escalations_dir
        self._emit = emit
        self._transcriber = transcriber or NoTranscriber()
        self._history_turns = history_turns
        self._framing = framing or FramingConfig(enabled=False)
        self._calendar = calendar or NoCalendar()
        self._calendar_config = calendar_config or CalendarConfig()
        self._wiki = wiki
        self._notebook = notebook
        self._wiki_timeout = wiki_timeout
        self._wiki_notice_after = wiki_notice_after
        self._wiki_mention_source = wiki_mention_source
        self._last_looking_up: str | None = None
        self._wiki_topic: str | None = None  # the article the last lookup named
        self._now = now
        # When she asked "what should I write down?" (the next message is the note).
        self._note_asked: float | None = None
        # The event she just read back (to add, or to remove), waiting for a yes,
        # and when she asked.
        self._pending: tuple[EventDraft | FoundEvent, float] | None = None

        self.briefs: dict[str, Brief] = {}
        self.current: Brief | None = None
        self.history: list[Turn] = []
        self._seq = 0
        self._reply_ids = 0
        self._task: asyncio.Task | None = None

    # ---- brief pickup -------------------------------------------------------

    def ready_brief(self) -> Brief | None:
        """The newest brief that hasn't been read out yet."""
        unhandled = [b for b in self.briefs.values() if not self._state.is_handled(b.id)]
        return max(unhandled, key=lambda b: b.generated_at, default=None)

    def context_brief(self) -> Brief | None:
        """The brief questions are about: the one read out in this session, or
        else the newest one, so she knows today's brief after a restart (or when
        it was read out earlier in another window)."""
        if self.current is not None:
            return self.current
        return max(self.briefs.values(), key=lambda b: b.generated_at, default=None)

    async def poll(self) -> None:
        new = self._scanner.scan()
        for brief in new:
            self.briefs[brief.id] = brief
            if brief.answers_escalations:
                done = escalations.mark_answered(
                    self._escalations_dir, brief.answers_escalations, brief.id
                )
                if done:
                    log.info("Brief %s answered escalations %s", brief.id, done)
        if new:
            await self.send_briefs()

    async def send_briefs(self) -> None:
        ready = self.ready_brief()
        listing = sorted(self.briefs.values(), key=lambda b: b.generated_at, reverse=True)
        await self._emit(
            {
                "type": "briefs",
                "ready": ready.id if ready else None,
                "briefs": [
                    {
                        "id": b.id,
                        "type": b.type.value,
                        "date": b.date.isoformat(),
                        "handled": self._state.is_handled(b.id),
                        "sample": b.sample,
                    }
                    for b in listing
                ],
            }
        )

    # ---- user actions -------------------------------------------------------

    def start_brief(self, brief_id: str | None = None) -> None:
        self._run(self._read_brief(brief_id))

    def ask(self, text: str) -> None:
        self._run(self._answer(text))

    def voice(self, audio: bytes) -> None:
        """Push-to-talk: transcribe, show what was heard, then answer it."""
        self._run(self._voice(audio))

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    def _run(self, coro: Awaitable[None]) -> None:
        """One thing at a time: a new action interrupts whatever is speaking."""
        self.stop()
        self._task = asyncio.create_task(self._guarded(coro))

    async def _guarded(self, coro: Awaitable[None]) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # report, don't kill the server
            log.exception("action failed")
            await self._emit({"type": "error", "message": str(exc)})

    # ---- pipelines ----------------------------------------------------------

    async def _read_brief(self, brief_id: str | None) -> None:
        brief = self.briefs.get(brief_id) if brief_id else self.ready_brief()
        if brief is None:
            await self._emit({"type": "error", "message": "No brief to read."})
            return
        self.current = brief
        self.history = []  # a new brief starts a new conversation
        self._state.mark_handled(brief.id)
        await self._emit(
            {
                "type": "readout",
                "brief": {
                    "id": brief.id,
                    "type": brief.type.value,
                    "date": brief.date.isoformat(),
                    "display": brief.display,
                    "sample": brief.sample,
                },
            }
        )
        await self.send_briefs()
        # The spoken script is read verbatim. It never passes through the local
        # model, so the persona can't distort the facts in the readout. Her own
        # framing lines (if on) only go before, between and after paragraphs.
        framer = None
        if self._framing.enabled:
            framer = BriefFramer(self._model, self.persona, brief, self._framing)
            framer.start()
        try:
            await self._express("neutral")
            parts = paragraphs(brief.spoken)
            # Her opener comes right after the brief's own "Good morning, Cooper!"
            # (or first, if there's no greeting), so she doesn't talk before saying hi.
            greeting, parts[0] = split_greeting(parts[0])
            for sentence in split_sentences(greeting):
                await self._say(sentence)
            if framer and (opener := await framer.opener()):
                await self._say_frame(opener)
            # Likewise her closing line goes before the brief's own sign-off.
            parts[-1], signoff = split_signoff(parts[-1])
            for n, paragraph in enumerate(parts, 1):
                for sentence in split_sentences(paragraph):
                    await self._say(sentence)
                if framer and (aside := await framer.aside(n)):
                    await self._say_frame(aside)
            if framer and (closer := await framer.closer()):
                await self._say_frame(closer)
            for sentence in split_sentences(signoff):
                await self._say(sentence)
        finally:
            if framer:
                framer.cancel()
        self.history.append(Turn("assistant", brief.spoken))
        await self._emit({"type": "speech_done"})

    async def _say_frame(self, line: FrameLine) -> None:
        if line.mood:
            await self._express(line.mood)
        await self._say(line.text)

    async def _voice(self, audio: bytes) -> None:
        start = time.perf_counter()
        try:
            hints = speech_hints(self.context_brief(), self.persona)
            text = await self._transcriber.transcribe(audio, hints)
        except SttUnavailable as exc:
            await self._emit({"type": "transcript", "text": ""})
            await self._emit({"type": "error", "message": str(exc)})
            return
        log.info("Transcribed %d bytes in %.1f s", len(audio), time.perf_counter() - start)
        await self._emit({"type": "transcript", "text": text})
        if text:
            await self._answer(text)

    async def _answer(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if await self._notes_turn(text):
            return
        if await self._calendar_turn(text):
            return
        if escalations.wants_escalation(text):
            await self._escalate(text)
            return
        if (req := commands.brief_request(text)) is not None:
            await self._read_requested(req)
            return

        messages = build_messages(
            self.persona,
            self.context_brief(),
            self.history,
            text,
            history_turns=self._history_turns,
            wikipedia=await self._wikipedia_for(text),
            mention_source=self._wiki_mention_source,
        )
        rid = self._next_reply_id()
        await self._emit({"type": "reply_start", "id": rid})
        splitter = SentenceSplitter()
        tags = TagFilter()  # pulls "[happy]" etc. out before display and speech
        speech: asyncio.Queue[str | None] = asyncio.Queue()
        speaking = asyncio.create_task(self._speak_queue(speech))
        reply: list[str] = []
        expressed = False
        start = time.perf_counter()

        async def show(text: str, moods: list[str]) -> None:
            nonlocal expressed
            for mood in moods:
                await self._express(mood)
                expressed = True
            if not text:
                return
            if not expressed:  # the model skipped the tag: fall back to neutral
                await self._express("neutral")
                expressed = True
            reply.append(text)
            await self._emit({"type": "reply_token", "id": rid, "text": text})
            for sentence in splitter.feed(text):
                speech.put_nowait(sentence)

        try:
            first = True
            async for token in self._model.stream_chat(
                messages, self.persona.sampling.as_options()
            ):
                if first:
                    log.info("First token after %.1f s", time.perf_counter() - start)
                    first = False
                await show(*tags.feed(token))
            await show(tags.flush(), [])
        except asyncio.CancelledError:
            speaking.cancel()  # interrupted: stop the speech pipeline too
            partial = "".join(reply).strip()
            await self._emit(
                {"type": "reply_end", "id": rid, "text": f"{partial} (stopped)".strip()}
            )
            raise
        except LlmUnavailable as exc:
            log.warning("model unavailable: %s", exc)
            reply = []
            splitter = SentenceSplitter()  # drop any half-finished sentence
            tags = TagFilter()
            await show(self.persona.lines.model_unavailable, ["neutral"])
        finally:
            for sentence in splitter.flush():
                speech.put_nowait(sentence)
            speech.put_nowait(None)

        full = "".join(reply).strip()
        await self._emit({"type": "reply_end", "id": rid, "text": full})
        if tags.mood:
            # Keep the tag in history: seeing it in her earlier replies helps a
            # small model keep using the format.
            full = f"[{tags.mood}] {full}"
        self.history += [Turn("user", text), Turn("assistant", full)]
        await speaking
        await self._emit({"type": "speech_done"})

    async def _wikipedia_for(self, text: str) -> str | None:
        """Passages for a general-knowledge question; "" when it was looked up but
        nothing usable came back (timeout, failure, no match), so she answers
        from what she knows as a hedged best guess; None when the question
        isn't one to look up."""
        if self._wiki is None or (request := lookup_request(text)) is None:
            return None
        started = time.perf_counter()
        last_reply = next(
            (t.content for t in reversed(self.history) if t.role == "assistant"), None
        )
        query = follow_up(request.query, self._wiki_topic, last_reply and strip_tags(last_reply))
        # Loose (any-word) matches are only kept for an explicit "look up", and
        # finding them is the slow part, so skip them otherwise.
        search = asyncio.ensure_future(
            asyncio.to_thread(self._wiki.search, query, 8, loose=request.forced)
        )
        notice = min(self._wiki_notice_after, self._wiki_timeout)
        try:
            try:
                found = await asyncio.wait_for(asyncio.shield(search), notice)
            except TimeoutError:
                # Still looking: say so, then keep waiting for the rest of the time.
                await self._interject(self._looking_up_line())
                left = self._wiki_timeout - (time.perf_counter() - started)
                found = await asyncio.wait_for(search, max(left, 0.01))
        except TimeoutError:
            log.warning(
                "Wikipedia lookup took over %.0f s; answering without it", self._wiki_timeout
            )
            # The search thread is still running: stop it, or the next question
            # waits behind it.
            if interrupt := getattr(self._wiki, "interrupt", None):
                interrupt()
            # It ends with an "interrupted" error nobody waits for: keep it quiet.
            search.add_done_callback(lambda f: f.cancelled() or f.exception())
            return ""
        except Exception:
            log.exception("Wikipedia lookup failed")
            return ""
        passages = usable(found, request)
        named = next((p.title for p in passages if p.match == "title"), None)
        if named:
            self._wiki_topic = named  # for a follow-up like "what about his kids?"
        log.info(
            "Wikipedia: %d of %d passages kept in %.0f ms (%s) for %r",
            len(passages),
            len(found),
            (time.perf_counter() - started) * 1000,
            ", ".join(p.title for p in passages) or "nothing",
            query,
        )
        return render(passages) if passages else ""

    def _looking_up_line(self) -> str:
        """A "one moment" line, not the same as last time when there's a choice."""
        options = self.persona.lines.looking_up or ["One moment."]
        fresh = [line for line in options if line != self._last_looking_up] or options
        self._last_looking_up = random.choice(fresh)
        return self._last_looking_up

    async def _interject(self, line: str) -> None:
        """A short line before the real answer ("one moment"). Shown and spoken, but
        not kept in the chat history, so the model never sees it."""
        rid = self._next_reply_id()
        await self._emit({"type": "reply_start", "id": rid})
        await self._emit({"type": "reply_end", "id": rid, "text": line})
        await self._express("neutral")
        await self._say(line)

    async def _read_requested(self, req: commands.BriefRequest) -> None:
        """ "Read me this morning's brief": read it out verbatim, never via the model."""
        brief = commands.find_brief(req, self.briefs.values(), dt.date.today())
        if brief is not None:
            await self._read_brief(brief.id)
            return
        line = f"I don't have {commands.describe(req)}."
        rid = self._next_reply_id()
        await self._emit({"type": "reply_start", "id": rid})
        await self._emit({"type": "reply_end", "id": rid, "text": line})
        await self._express("neutral")
        await self._say(line)
        await self._emit({"type": "speech_done"})

    # ---- notes ----------------------------------------------------------------

    NOTE_ANSWER_SECONDS = 120.0

    async def _notes_turn(self, text: str) -> bool:
        """ "Write that down: ..." and friends. True if handled. The note is written
        exactly as heard; the model never sees or rewrites it."""
        lines = self.persona.lines
        asked, self._note_asked = self._note_asked, None
        if asked is not None and time.monotonic() - asked <= self.NOTE_ANSWER_SECONDS:
            if is_cancel(text):
                await self._reply_line(lines.note_cancelled, text)
                return True
            if note_request(text, self.persona.name) is None:
                await self._write_note(clean_note(text), text)
                return True
        if escalations.wants_escalation(text):
            return False
        request = note_request(text, self.persona.name)
        if request is None:
            return False
        notebook = self._notebook
        if notebook is None or not notebook.enabled:
            await self._reply_line(lines.notes_off, text)
            return True
        await self._note_action(notebook, request, text)
        return True

    async def _note_action(self, notebook: Notebook, request: NoteRequest, text: str) -> None:
        lines = self.persona.lines
        if request.kind == "write":
            if request.text:
                await self._write_note(request.text, text)
            else:
                self._note_asked = time.monotonic()
                await self._reply_line(lines.note_ask, text)
            return
        try:
            if request.kind == "new_page":
                page = await asyncio.to_thread(notebook.new_page, request.text)
                line = lines.note_page_started.format(page=page.stem)
                if notebook.viewer and not await asyncio.to_thread(notebook.open, page):
                    line += " " + lines.note_open_failed.format(viewer=notebook.viewer)
            elif request.kind == "where":
                page = notebook.current_page()
                line = lines.note_where.format(page=page.stem, path=page)
            elif request.kind == "read":
                notes = await asyncio.to_thread(notebook.last_notes)
                page = notebook.current_page().stem
                line = (
                    lines.note_read.format(page=page, notes="; ".join(notes))
                    if notes
                    else lines.note_read_empty.format(page=page)
                )
            elif await asyncio.to_thread(notebook.open):
                line = lines.note_opened
            elif notebook.viewer:
                line = lines.note_open_failed.format(viewer=notebook.viewer)
            else:
                line = lines.note_no_viewer.format(folder=notebook.dir)
        except OSError as exc:
            line = lines.note_failed.format(reason=exc.strerror or exc)
        await self._reply_line(line, text)

    async def _write_note(self, note: str, text: str) -> None:
        notebook = self._notebook
        if notebook is None or not notebook.enabled:
            await self._reply_line(self.persona.lines.notes_off, text)
            return
        try:
            page = await asyncio.to_thread(notebook.write, note)
        except OSError as exc:
            reason = exc.strerror or str(exc)
            await self._reply_line(self.persona.lines.note_failed.format(reason=reason), text)
            return
        await self._emit({"type": "note_added", "page": page.stem, "text": note})
        await self._reply_line(self.persona.lines.note_written.format(note=note), text)

    # ---- calendar -------------------------------------------------------------

    async def _calendar_turn(self, text: str) -> bool:
        """Handle a calendar request, or the yes/no to one. True if handled.

        Like brief requests, this never goes through the model: the event is
        parsed in code, read back, and only added after an explicit yes.
        """
        lines = self.persona.lines
        pending, self._pending = self._pending, None
        if pending is not None:
            draft, asked = pending
            fresh = time.monotonic() - asked <= self._calendar_config.confirm_seconds
            answer = answer_to_confirm(text, self.persona.name) if fresh else None
            if answer is True:
                if isinstance(draft, FoundEvent):
                    await self._remove_event(draft, text)
                else:
                    await self._add_event(draft, text)
                return True
            if answer is False:
                await self._reply_line(lines.calendar_cancelled, text)
                return True
            # Anything else drops the read-back and is handled as a new message.

        if escalations.wants_escalation(text):
            return False
        now = self._now()
        agenda = parse_agenda(text, now, name=self.persona.name)
        if agenda is not None:
            await self._agenda_turn(agenda, text, now)
            return True
        removal = parse_removal(text, now, name=self.persona.name)
        if removal is not None:
            await self._removal_turn(removal, text, now)
            return True
        result = parse_event(
            text,
            now,
            default_minutes=self._calendar_config.default_minutes,
            name=self.persona.name,
        )
        if result is None:
            return False
        if not self._calendar.available:
            await self._reply_line(lines.calendar_off, text)
        elif isinstance(result, Problem):
            await self._reply_line(result.message, text)
        else:
            self._pending = (result, time.monotonic())
            event = describe_event(result, now.date())
            await self._reply_line(lines.calendar_confirm.format(event=event), text)
        return True

    async def _agenda_turn(self, request, text: str, now: dt.datetime) -> None:
        """ "What's on my calendar tomorrow": read the day out, built in code from the
        calendar's own data (the model never summarizes it)."""
        lines = self.persona.lines
        if not self._calendar.available:
            await self._reply_line(lines.calendar_off, text)
            return
        if isinstance(request, Problem):
            await self._reply_line(request.message, text)
            return
        try:
            events = await self._calendar.agenda(request)
        except CalendarError as exc:
            await self._reply_line(lines.calendar_read_failed.format(reason=exc), text)
            return
        day = day_phrase(request.day, now.date())
        if not events:
            await self._reply_line(lines.calendar_clear.format(day=day), text)
            return
        shown = events[:8]
        listing = "; ".join(describe_agenda_item(e) for e in shown)
        if len(events) > len(shown):
            listing += f"; and {len(events) - len(shown)} more"
        things = f"{len(events)} thing" + ("" if len(events) == 1 else "s")
        await self._reply_line(
            lines.calendar_agenda.format(things=things, day=day, events=listing), text
        )

    async def _removal_turn(self, request, text: str, now: dt.datetime) -> None:
        """Find the one event of hers the request means and read it back."""
        lines = self.persona.lines
        if not self._calendar.available:
            await self._reply_line(lines.calendar_off, text)
            return
        if isinstance(request, Problem):
            await self._reply_line(request.message, text)
            return
        try:
            matches = await self._calendar.find(request, now)
        except CalendarError as exc:
            await self._reply_line(lines.calendar_remove_failed.format(reason=exc), text)
            return
        if not matches:
            await self._reply_line(lines.calendar_remove_none, text)
        elif len(matches) > 1:
            shown = "; ".join(describe_event(m.draft, now.date()) for m in matches[:3])
            more = " and more" if len(matches) > 3 else ""
            await self._reply_line(
                f"I found {len(matches)} like that: {shown}{more}. "
                'Say the day too, like "remove the lunch on Friday".',
                text,
            )
        else:
            self._pending = (matches[0], time.monotonic())
            event = describe_event(matches[0].draft, now.date())
            await self._reply_line(lines.calendar_remove_confirm.format(event=event), text)

    async def _remove_event(self, found: FoundEvent, text: str) -> None:
        try:
            await self._calendar.remove(found)
        except CalendarError as exc:
            reason = self.persona.lines.calendar_remove_failed.format(reason=exc)
            await self._reply_line(reason, text)
            return
        await self._emit({"type": "calendar_removed", "title": found.draft.title})
        await self._reply_line(self.persona.lines.calendar_removed, text)

    async def _add_event(self, draft: EventDraft, text: str) -> None:
        try:
            link = await self._calendar.add(draft, added_by=self.persona.name)
        except CalendarError as exc:
            await self._reply_line(self.persona.lines.calendar_failed.format(reason=exc), text)
            return
        await self._emit({"type": "calendar_added", "title": draft.title, "link": link})
        await self._reply_line(self.persona.lines.calendar_added, text)

    async def _reply_line(self, line: str, user_text: str) -> None:
        """A fixed reply (not from the model): show it, say it, keep it in history."""
        rid = self._next_reply_id()
        await self._emit({"type": "reply_start", "id": rid})
        await self._emit({"type": "reply_end", "id": rid, "text": line})
        self.history += [Turn("user", user_text), Turn("assistant", line)]
        await self._express("neutral")
        await self._say(line)
        await self._emit({"type": "speech_done"})

    async def _escalate(self, text: str) -> None:
        question = escalations.strip_command(text) or text
        context = [
            (
                self.persona.user_name if t.role == "user" else self.persona.name,
                strip_tags(t.content),
            )
            for t in self.history[-4:]
            if not (self.current and t.content == self.current.spoken)  # skip the readout
        ]
        esc = escalations.new_escalation(
            question, about_brief=self.current.id if self.current else None, context=context
        )
        path = escalations.write(esc, self._escalations_dir)
        log.info("Wrote escalation %s", path.name)
        await self._emit({"type": "escalated", "id": esc.id, "question": question})
        line = self.persona.lines.escalated
        rid = self._next_reply_id()
        await self._emit({"type": "reply_start", "id": rid})
        await self._emit({"type": "reply_end", "id": rid, "text": line})
        self.history += [Turn("user", text), Turn("assistant", line)]
        await self._express("neutral")
        await self._say(line)
        await self._emit({"type": "speech_done"})

    def _next_reply_id(self) -> int:
        self._reply_ids += 1
        return self._reply_ids

    async def _express(self, mood: str) -> None:
        look = self.persona.expression(mood)
        await self._emit(
            {
                "type": "expression",
                "mood": mood,
                "faces": look.faces(),
                "talk_faces": look.talk_faces(),
            }
        )

    async def _speak_queue(self, queue: asyncio.Queue[str | None]) -> None:
        while (sentence := await queue.get()) is not None:
            await self._say(sentence)

    async def _say(self, sentence: str) -> None:
        audio = await self._speaker.synthesize(sentence, self.persona.voice)
        self._seq += 1
        await self._emit(
            {
                "type": "say",
                "seq": self._seq,
                "text": sentence,
                "audio": base64.b64encode(audio).decode("ascii") if audio else None,
            }
        )
