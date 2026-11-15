"""Live notes: a word-for-word transcript of a call or interview.

While a session runs she listens to your microphone and to what the computer
plays, transcribes each phrase with Whisper, and appends it to a notes page:

    - [14:02] **Computer:** So tell me about a project you're proud of.
    - [14:02] **You:** Sure. At my last job I rebuilt the outage map...

The transcript is written exactly as heard: the chat model never sees or
rewrites these lines. (A summary in her own words is a separate thing.)

How the pieces fit:

    mic / loopback thread --blocks--> Segmenter --chunks--> Whisper --lines--> hold --> page

Lines wait `hold_seconds` before they're written, so lines from both sources go
in time order, a speaker's back-to-back chunks join into one line, and (with
speakers instead of headphones) a "You" line that only repeats the call can be
left out. Audio is kept in memory only: the last few seconds per source, for a
later "write that down" with nothing after it.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import difflib
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from .config import LiveNotesConfig
from .live_audio import BlockHandler, Chunk, RecentAudio, Segmenter, SoundcardSource
from .live_summary import LiveSummary
from .llm import ChatModel
from .notes import _END, _FILLER, _POLITE, Notebook, page_file_name
from .stt import SttUnavailable, Transcriber

log = logging.getLogger(__name__)

# A speaker's next chunk joins their line if it starts within this many
# seconds, up to these limits, so a long answer still shows up while it goes on.
JOIN_GAP = 1.5
LINE_MAX_SECONDS = 45.0
LINE_MAX_CHARS = 600
PROMPT_CHARS = 200  # how much of the previous text Whisper gets as context
# Her own speech, padded for the browser's playback delay and room echo.
OWN_SPEECH_LEAD = 0.3
OWN_SPEECH_TAIL = 0.6


class LiveNotesUnavailable(RuntimeError):
    pass


class AudioSource(Protocol):
    source: str  # "mic" or "computer"
    error: str | None

    def start(self, on_block: BlockHandler) -> None:
        """Start delivering blocks (from any thread). Raises if the device is missing."""

    def stop(self) -> None: ...


@dataclass
class Line:
    source: str
    start: float
    end: float
    text: str
    ready: float  # when it may be written (wall clock)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def is_echo(mic: str, computer: str) -> bool:
    """Whether a microphone line is just the call coming out of the speakers."""
    a, b = _words(mic), _words(computer)
    if not a or not b:
        return False
    if difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() >= 0.6:
        return True
    # A chunk of the mic heard part of a longer computer line.
    found = set(b)
    return len(a) >= 3 and sum(w in found for w in a) / len(a) >= 0.75


def page_name(template: str, now: dt.datetime) -> str:
    return template.replace("{date}", f"{now:%Y-%m-%d}").replace("{time}", f"{now:%H%M}")


def make_sources(config: LiveNotesConfig) -> list[AudioSource]:
    try:
        import soundcard  # noqa: F401
    except Exception as exc:  # ImportError, or no audio system (Linux without Pulse)
        raise LiveNotesUnavailable(
            "live notes need the live extra: uv sync --extra live (with your other extras)"
        ) from exc
    sources: list[AudioSource] = []
    if config.microphone:
        sources.append(SoundcardSource("mic", config.microphone_device))
    if config.computer_audio:
        sources.append(SoundcardSource("computer", config.speaker_device))
    return sources


class LiveSession:
    """One live-notes session. `start` once, `stop` once."""

    def __init__(
        self,
        config: LiveNotesConfig,
        transcriber: Transcriber,
        notebook: Notebook,
        sources: list[AudioSource],
        on_line: Callable[[Line, str], Awaitable[None]] | None = None,
        clock: Callable[[], float] = time.time,
        tick_seconds: float = 0.25,
    ):
        self._config = config
        self._transcriber = transcriber
        self._notebook = notebook
        self._sources = sources
        self._on_line = on_line
        self._clock = clock
        self._tick = tick_seconds
        self._labels = {"mic": config.you_label, "computer": config.computer_label}
        self._segmenters = {
            s.source: Segmenter(
                s.source,
                pause_seconds=config.pause_seconds,
                max_seconds=config.max_chunk_seconds,
                min_rms=config.min_level,
            )
            for s in sources
        }
        self.recent = {s.source: RecentAudio(config.recent_seconds) for s in sources}
        self._blocks: asyncio.Queue[tuple[str, np.ndarray, float]] = asyncio.Queue()
        self._chunks: asyncio.Queue[Chunk | None] = asyncio.Queue()
        self._held: list[Line] = []
        self._heard: dict[str, str] = {}  # the last text per source, for Whisper's context
        self._computer_said: list[Line] = []  # recent computer lines, for the echo check
        self._tasks: list[asyncio.Task] = []
        self._stopping = False
        self.page: Path | None = None
        self.started: float | None = None
        self.listening: list[str] = []  # sources that started
        self.lines: list[tuple[Line, str]] = []  # (line, label) as written
        self.problems: list[str] = []
        self._writing = asyncio.Lock()
        # Her own sentences as they played: (start, end or None while playing, text).
        self._own: list[tuple[float, float | None, str]] = []

    @property
    def running(self) -> bool:
        return bool(self._tasks)

    async def start(self, page: Path) -> None:
        if not getattr(self._transcriber, "available", False):
            raise LiveNotesUnavailable("speech recognition isn't loaded")
        loop = asyncio.get_running_loop()
        for source in self._sources:

            def handler(samples, end, name=source.source):
                self.recent[name].add(samples, end)
                loop.call_soon_threadsafe(self._blocks.put_nowait, (name, samples, end))

            try:
                source.start(handler)
                self.listening.append(source.source)
            except Exception as exc:
                log.warning("Live notes can't hear %s: %s", source.source, exc)
                self.problems.append(f"{self._labels[source.source]}: {exc}")
        if not self.listening:
            raise LiveNotesUnavailable(
                "no audio device could be opened: " + "; ".join(self.problems)
            )
        self.page = page
        self.started = self._clock()
        self._tasks = [
            asyncio.create_task(self._pump()),
            asyncio.create_task(self._transcribe_loop()),
            asyncio.create_task(self._ticker()),
        ]
        log.info("Live notes on %s, listening to %s", page.name, ", ".join(self.listening))

    async def stop(self) -> None:
        """Stop listening, transcribe what's left, and write every held line."""
        if not self._tasks:
            return
        for source in self._sources:
            await asyncio.to_thread(source.stop)
        # Blocks the capture threads handed over just before they stopped are
        # still queued on the event loop: let those land first (FIFO).
        landed = asyncio.Event()
        asyncio.get_running_loop().call_soon_threadsafe(landed.set)
        await landed.wait()
        self._drain_blocks()
        for seg in self._segmenters.values():
            for chunk in seg.flush():
                self._chunks.put_nowait(chunk)
        self._chunks.put_nowait(None)
        pump, transcribe, ticker = self._tasks
        pump.cancel()  # only ever waits for the next block
        with contextlib.suppress(asyncio.CancelledError):
            await pump
        # The ticker may be writing a line: let it finish that, never cut it off.
        self._stopping = True
        await ticker
        await transcribe
        self._tasks = []
        await self._flush(everything=True)
        for source in self._sources:
            if source.error:
                self.problems.append(f"{self._labels[source.source]}: {source.error}")
        for buffer in self.recent.values():
            buffer.clear()
        log.info("Live notes stopped: %d lines on %s", len(self.lines), self.page)

    # ---- the pipeline --------------------------------------------------------

    def _drain_blocks(self) -> None:
        while not self._blocks.empty():
            name, samples, end = self._blocks.get_nowait()
            self._feed(name, samples, end)

    def _feed(self, name: str, samples: np.ndarray, end: float) -> None:
        for chunk in self._segmenters[name].feed(samples, end):
            self._chunks.put_nowait(chunk)

    async def _pump(self) -> None:
        while True:
            name, samples, end = await self._blocks.get()
            self._feed(name, samples, end)

    async def _transcribe_loop(self) -> None:
        while (chunk := await self._chunks.get()) is not None:
            try:
                text = await self._transcriber.transcribe_samples(
                    chunk.samples, self._heard.get(chunk.source, "")[-PROMPT_CHARS:]
                )
            except SttUnavailable as exc:
                self.problems.append(str(exc))
                continue
            except Exception:
                log.exception("live transcription failed")
                continue
            if not text or self._is_own_voice(chunk, text):
                continue
            self._heard[chunk.source] = f"{self._heard.get(chunk.source, '')} {text}"[-1000:]
            line = Line(
                chunk.source,
                chunk.start,
                chunk.end,
                text,
                self._clock() + self._config.hold_seconds,
            )
            self._held.append(line)
            if chunk.source == "computer":
                self._computer_said.append(line)

    def own_speech(self, speaking: bool, text: str = "") -> None:
        """She started (or finished) saying a sentence out loud."""
        now = self._clock()
        if self._own and self._own[-1][1] is None:
            start, _, said = self._own[-1]
            self._own[-1] = (start, now + OWN_SPEECH_TAIL, said)
        if speaking:
            self._own.append((now - OWN_SPEECH_LEAD, None, text))
        self._own = [o for o in self._own if o[1] is None or o[1] > now - 120]

    def _is_own_voice(self, chunk: Chunk, text: str) -> bool:
        """Her own voice coming back through the loopback (or the mic, with
        speakers): it isn't part of the conversation."""
        for start, end, said in self._own:
            end = self._clock() if end is None else end
            overlap = min(end, chunk.end) - max(start, chunk.start)
            if overlap <= 0:
                continue
            if chunk.source == "computer" and overlap >= 0.5 * chunk.seconds:
                return True
            if is_echo(text, said):
                return True
        return False

    async def _ticker(self) -> None:
        while not self._stopping:
            await asyncio.sleep(self._tick)
            if self._stopping:
                break
            now = self._clock()
            for seg in self._segmenters.values():
                for chunk in seg.idle(now):
                    self._chunks.put_nowait(chunk)
            await self._flush()

    async def _flush(self, everything: bool = False) -> None:
        """Write the held lines that are ready, oldest first."""
        async with self._writing:
            now = self._clock()
            # Mic lines that only repeat the call go first, before they can join
            # a real line of yours.
            self._held = [h for h in self._held if not (h.source == "mic" and self._echoes(h))]
            self._held.sort(key=lambda line: line.start)
            while self._held:
                line = self._held[0]
                if not everything and line.ready > now:
                    break
                if not everything and self._still_talking(line, now):
                    break  # their next words may join this line
                self._held.pop(0)
                line = self._join_next(line, everything, now)
                await self._write(line)
            horizon = now - 60
            self._computer_said = [c for c in self._computer_said if c.end >= horizon]

    def _still_talking(self, line: Line, now: float) -> bool:
        """The same speaker kept going right after this line, and it isn't long yet."""
        if line.end - line.start >= LINE_MAX_SECONDS or len(line.text) >= LINE_MAX_CHARS:
            return False
        if now - line.end > LINE_MAX_SECONDS:
            return False
        seg = self._segmenters.get(line.source)
        since = seg.open_since if seg else None
        return since is not None and since - line.end <= JOIN_GAP

    def _join_next(self, line: Line, everything: bool, now: float) -> Line:
        """Merge the same speaker's following ready lines into this one, as long
        as nobody else spoke in between (the held lines are in time order)."""
        while self._held:
            nxt = self._held[0]
            if nxt.source != line.source or nxt.start - line.end > JOIN_GAP:
                return line
            if not everything and nxt.ready > now:
                return line
            if nxt.end - line.start > LINE_MAX_SECONDS or len(line.text) >= LINE_MAX_CHARS:
                return line
            self._held.pop(0)
            line = Line(line.source, line.start, nxt.end, f"{line.text} {nxt.text}", line.ready)
        return line

    def _echoes(self, line: Line) -> bool:
        if not self._config.echo_check or "computer" not in self.listening:
            return False
        echo = any(
            c.start <= line.end + 2 and c.end >= line.start - 2 and is_echo(line.text, c.text)
            for c in self._computer_said
        )
        if echo:
            log.debug("Left out a mic line that repeats the computer: %s", line.text)
        return echo

    async def _write(self, line: Line) -> None:
        label = self._labels[line.source]
        at = dt.datetime.fromtimestamp(line.start)
        try:
            await asyncio.to_thread(
                self._notebook.write, f"**{label}:** {line.text}", self.page, at
            )
        except OSError as exc:
            log.warning("Couldn't write a live note: %s", exc)
            self.problems.append(str(exc))
            return
        self.lines.append((line, label))
        if self._on_line is not None:
            await self._on_line(line, label)


# ---- starting and stopping -----------------------------------------------------

# "start live notes", "take a live transcript", "transcribe this call",
# "listen in on this interview", "start transcribing". Anything worded another
# way goes to her, and she can start or stop it with a [live notes: ...] tag.
_LIVE_THING = (
    r"(?:(?:a\s+)?live\s+(?:notes?|transcripts?|transcription)|(?:a\s+)?transcript(?:ion)?"
    r"|(?:(?:a\s+|some\s+)?(?:notes?|transcripts?)\s+(?:of|on)\s+(?:this|the|my|our)\s+)"
    r"(?:call|meeting|interview|conversation|video|chat))"
)
_CALL = r"(?:this|the|my|our)\s+(?:call|meeting|interview|conversation|video|chat)"
_START = re.compile(
    rf"^\s*{_FILLER}{_POLITE}"
    r"(?:"
    r"(?:(?:start|begin)\s+(?:taking|doing|keeping)|start|begin|turn\s+on|switch\s+on"
    r"|take|do|keep|run)\s+(?:the\s+|some\s+)?"
    + _LIVE_THING
    + r"|start\s+(?:transcribing|listening\s+in)(?:\s+(?:on|to))?(?:\s+"
    + _CALL
    + r")?"
    + r"|(?:transcribe|listen\s+in\s+(?:on|to))\s+"
    + _CALL
    + r")"
    rf"{_END}(?P<rest>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_STOP = re.compile(
    rf"^\s*{_FILLER}{_POLITE}"
    r"(?:stop|end|turn\s+off|switch\s+off|finish|quit)\s+(?:the\s+|taking\s+(?:the\s+)?)?"
    r"(?:live\s+(?:notes?|transcripts?|transcription)|transcri(?:pt|ption|bing)|listening(?:\s+in)?)"
    r"(?:\s+(?:now|please|for\s+now))*[\s.!?]*$",
    re.IGNORECASE,
)
_HIDDEN = re.compile(
    r"(?:\b(?:and|but)\s+)?(?:\b(?:stay|keep|be|remain)\s+)?(?:in\s+)?"
    r"\b(?:hidden|hide|quietly|silently|in\s+the\s+background|out\s+of\s+sight"
    r"|(?:without|don't|do\s+not)\s+(?:showing|show|being\s+seen|talking|saying))\b[^,.;]*",
    re.IGNORECASE,
)
_NAMED = re.compile(
    r"^[\s,:;-]*(?:on\s+a\s+page\s+)?(?:for|called|named|titled|about)\s+(?:the\s+|my\s+|our\s+)?"
    r"(?P<name>[^.!?]+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LiveRequest:
    action: str  # "start" or "stop"
    name: str = ""  # the page name, if one was given
    hidden: bool = False


def live_request(text: str) -> LiveRequest | None:
    if _STOP.match(text):
        return LiveRequest("stop")
    m = _START.match(text)
    if m is None:
        return None
    rest = m["rest"]
    hidden = bool(_HIDDEN.search(rest))
    rest = _HIDDEN.sub("", rest)
    named = _NAMED.match(rest)
    name = named["name"].strip(" ,;:-\"'“”") if named else ""
    name = re.sub(r"\s+(?:and|while|please)\s*$", "", name, flags=re.IGNORECASE).strip()
    return LiveRequest("start", name, hidden)


def tag_request(text: str) -> LiveRequest:
    """[live notes: start], [live notes: start hidden, Acme call], [live notes: stop]"""
    what, _, name = text.partition(",")
    if re.search(r"\b(?:stop|end|off)\b", what, re.IGNORECASE):
        return LiveRequest("stop")
    return LiveRequest("start", name.strip(), bool(_HIDDEN.search(what)))


class LiveNotes:
    """Starts and stops live-notes sessions for the app (one at a time) and
    keeps her own voice out of the transcript."""

    def __init__(
        self,
        config: LiveNotesConfig,
        transcriber: Transcriber,
        notebook: Notebook,
        emit: Callable[[dict], Awaitable[None]],
        sources: Callable[[LiveNotesConfig], list[AudioSource]] = make_sources,
        now: Callable[[], dt.datetime] = dt.datetime.now,
        model: ChatModel | None = None,
        model_options: dict | None = None,
        user: str = "the user",
    ):
        self._config = config
        self._model = model
        self._model_options = model_options or {}
        self._user = user
        self.summary: LiveSummary | None = None
        self._summarizing: asyncio.Task | None = None
        self._final: asyncio.Task | None = None
        self._transcriber = transcriber
        self._notebook = notebook
        self._emit = emit
        self._make_sources = sources
        self._now = now
        self.session: LiveSession | None = None
        self.hidden = False
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self._config.enabled and self._notebook.enabled

    @property
    def on(self) -> bool:
        return self.session is not None and self.session.running

    async def start(self, name: str = "", hidden: bool = False) -> LiveSession:
        """Start a session on a new page (or the named one). Raises
        LiveNotesUnavailable with a reason she can say."""
        async with self._lock:
            if self.on:
                return self.session  # type: ignore[return-value]
            if not self.enabled:
                raise LiveNotesUnavailable("live notes are turned off in config.yaml")
            sources = self._make_sources(self._config)
            session = LiveSession(
                self._config, self._transcriber, self._notebook, sources, self._line_added
            )
            when = self._now()
            title = name or page_name(self._config.page, when)
            page = self._notebook.find_page(title) or self._notebook.dir / page_file_name(title)
            await session.start(page)
            # The live page becomes the page in use, so "what did he just ask?"
            # and "write that down" during the call both work on it.
            try:
                await asyncio.to_thread(self._notebook.new_page, title)
            except OSError:
                await session.stop()
                raise
            self.session, self.hidden = session, hidden
            self._start_summary(session, title)
            if not hidden and self._notebook.viewer:
                await asyncio.to_thread(self._notebook.open, page)
            await self._emit(self.status())
            return session

    async def stop(self) -> LiveSession | None:
        async with self._lock:
            session = self.session
            if session is None or not session.running:
                return None
            await session.stop()
            await self._emit(self.status())
            if self._summarizing is not None:
                self._summarizing.cancel()
                self._summarizing = None
            if self.summary is not None and session.lines:
                # The last update runs on its own, so stopping is instant.
                self._final = asyncio.create_task(self._final_summary(self.summary, session))
            return session

    # ---- her running summary ---------------------------------------------------

    def _start_summary(self, session: LiveSession, title: str) -> None:
        self.summary = None
        if not self._config.summary or self._model is None:
            return
        name = page_file_name(f"{title} summary")
        self.summary = LiveSummary(
            self._model,
            self._model_options,
            self._notebook.dir / name,
            self._user,
            (self._config.you_label, self._config.computer_label),
            self._config.summary_words,
            self._now,
        )
        self._summarizing = asyncio.create_task(self._summarize_loop(session, self.summary))

    @staticmethod
    def _lines(session: LiveSession) -> list[str]:
        return [f"{label}: {line.text}" for line, label in session.lines]

    async def _summarize_loop(self, session: LiveSession, summary: LiveSummary) -> None:
        while session.running:
            await asyncio.sleep(self._config.summary_every_seconds)
            if not session.running:
                break
            try:
                if await summary.update(self._lines(session)):
                    await self._emit({"type": "live_summary", "page": summary.page.stem})
            except OSError as exc:
                log.warning("Couldn't write the live summary: %s", exc)

    async def _final_summary(self, summary: LiveSummary, session: LiveSession) -> None:
        try:
            updated = await summary.update(self._lines(session), final=True)
            if not updated and summary.text:
                summary.write(final=True)  # nothing new since: just mark it final
            if summary.text:
                await self._emit({"type": "live_summary", "page": summary.page.stem, "final": True})
        except OSError as exc:
            log.warning("Couldn't write the live summary: %s", exc)
        except Exception:
            log.exception("live summary failed")

    def status(self) -> dict:
        s = self.session
        return {
            "type": "live_notes",
            "on": self.on,
            "hidden": self.hidden and self.on,
            "page": s.page.stem if s and s.page else None,
            "lines": len(s.lines) if s else 0,
        }

    def own_speech(self, speaking: bool, text: str = "") -> None:
        """The page started or stopped playing one of her sentences."""
        if self.session is not None:
            self.session.own_speech(speaking, text)

    async def _line_added(self, line: Line, label: str) -> None:
        await self._emit({"type": "live_line", "label": label, "text": line.text})

    async def aclose(self) -> None:
        await self.stop()
        if self._final is not None and not self._final.done():
            # Quitting: give the last summary a moment.
            with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(self._final, timeout=20)
