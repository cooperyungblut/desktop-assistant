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
    reply_start  {id}
    reply_token  {id, text}
    reply_end    {id, text}
    escalated    {id, question}
    error        {message}
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from . import escalations
from .briefs import Brief
from .llm import ChatModel, LlmUnavailable, SentenceSplitter, split_sentences
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
        # model, so the persona can't distort the facts in the readout.
        for sentence in split_sentences(brief.spoken):
            await self._say(sentence)
        self.history.append(Turn("assistant", brief.spoken))

    async def _voice(self, audio: bytes) -> None:
        start = time.perf_counter()
        try:
            hints = speech_hints(self.current, self.persona)
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
        if escalations.wants_escalation(text):
            await self._escalate(text)
            return

        messages = build_messages(
            self.persona, self.current, self.history, text, history_turns=self._history_turns
        )
        rid = self._next_reply_id()
        await self._emit({"type": "reply_start", "id": rid})
        splitter = SentenceSplitter()
        speech: asyncio.Queue[str | None] = asyncio.Queue()
        speaking = asyncio.create_task(self._speak_queue(speech))
        reply = []
        start = time.perf_counter()
        try:
            async for token in self._model.stream_chat(
                messages, self.persona.sampling.as_options()
            ):
                if not reply:
                    log.info("First token after %.1f s", time.perf_counter() - start)
                reply.append(token)
                await self._emit({"type": "reply_token", "id": rid, "text": token})
                for sentence in splitter.feed(token):
                    speech.put_nowait(sentence)
        except asyncio.CancelledError:
            speaking.cancel()  # interrupted: stop the speech pipeline too
            partial = "".join(reply).strip()
            await self._emit(
                {"type": "reply_end", "id": rid, "text": f"{partial} (stopped)".strip()}
            )
            raise
        except LlmUnavailable as exc:
            log.warning("model unavailable: %s", exc)
            reply = [self.persona.lines.model_unavailable]
            splitter = SentenceSplitter()  # drop any half-finished sentence
            speech.put_nowait(reply[0])
            await self._emit({"type": "reply_token", "id": rid, "text": reply[0]})
        finally:
            for sentence in splitter.flush():
                speech.put_nowait(sentence)
            speech.put_nowait(None)

        full = "".join(reply).strip()
        await self._emit({"type": "reply_end", "id": rid, "text": full})
        self.history += [Turn("user", text), Turn("assistant", full)]
        await speaking

    async def _escalate(self, text: str) -> None:
        question = escalations.strip_command(text) or text
        context = [
            (self.persona.user_name if t.role == "user" else self.persona.name, t.content)
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
        await self._say(line)

    def _next_reply_id(self) -> int:
        self._reply_ids += 1
        return self._reply_ids

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
