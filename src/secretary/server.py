"""FastAPI app: serves the web UI and a single WebSocket for events.

Client -> server messages:
    {"type": "start_brief", "id": optional brief id}
    {"type": "ask", "text": "..."}
    {"type": "voice", "audio": base64 audio from MediaRecorder}
    {"type": "stop"}
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import json
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from .assistant import Assistant
from .config import AppConfig
from .google_calendar import make_calendar
from .llm import LlmUnavailable, OllamaClient
from .persona import load_persona
from .state import StateStore
from .stt import make_transcriber
from .tts import make_speaker
from .watcher import BriefScanner
from .wiki import make_wiki

log = logging.getLogger(__name__)
MAX_VOICE_BYTES = 5_000_000  # about 5 minutes of Opus; push-to-talk clips are tiny
WEB_DIR = Path(__file__).resolve().parents[2] / "web"


class LocalStaticFiles(StaticFiles):
    """Static files the browser re-checks on every load.

    Without a Cache-Control header, browsers may reuse a cached index.html for
    hours, so after a `git pull` you could get the old page with the new
    scripts. "no-cache" still allows the cache, but revalidates it (a cheap
    304 on localhost).
    """

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


class Hub:
    """Fan events out to every connected browser tab."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def emit(self, event: dict[str, Any]) -> None:
        data = json.dumps(event)
        for ws in list(self.clients):
            try:
                await ws.send_text(data)
            except Exception:
                self.clients.discard(ws)


def create_app(config: AppConfig) -> FastAPI:
    hub = Hub()
    persona = load_persona(config.persona_file)
    model = OllamaClient(config.ollama)
    speaker = make_speaker(config.tts)
    transcriber = make_transcriber(config.stt)
    calendar = make_calendar(config.calendar)
    wiki = make_wiki(config.wiki)
    config.escalations_dir.mkdir(parents=True, exist_ok=True)
    assistant = Assistant(
        persona=persona,
        model=model,
        speaker=speaker,
        scanner=BriefScanner(config.briefs_dir),
        state=StateStore(config.state_file),
        escalations_dir=config.escalations_dir,
        emit=hub.emit,
        transcriber=transcriber,
        history_turns=config.history_turns,
        framing=config.brief_framing,
        calendar=calendar,
        calendar_config=config.calendar,
        wiki=wiki,
        wiki_timeout=config.wiki.timeout_seconds,
        wiki_notice_after=config.wiki.notice_after_seconds,
        wiki_mention_source=config.wiki.mention_source,
    )

    async def poll_forever() -> None:
        while True:
            try:
                await assistant.poll()
            except Exception:
                log.exception("poll failed")
            await asyncio.sleep(config.poll_seconds)

    async def status() -> dict[str, Any]:
        return {
            "type": "status",
            "persona": persona.name,
            "tts": speaker.available,
            "stt": transcriber.available,
            "model": await model.is_available(),
        }

    async def warm_up() -> None:
        """Load the chat model and Whisper in the background, then refresh the UI."""
        if await model.is_available():
            log.info("Loading %s into memory...", config.ollama.model)
            start = time.perf_counter()
            try:
                await model.warm_up(persona.sampling.as_options())
                log.info("Model ready in %.1f s", time.perf_counter() - start)
            except LlmUnavailable as exc:
                log.warning("%s", exc)
        if config.stt.engine != "none":
            log.info("Loading Whisper %s on %s...", config.stt.model, config.stt.device)
        await transcriber.load()
        await hub.emit(await status())

    unloaded = False

    async def unload() -> None:
        nonlocal unloaded
        if config.ollama.unload_on_exit and not unloaded:
            unloaded = True
            await model.unload()

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        log.info("Watching %s", config.briefs_dir)
        if not await model.is_available():
            log.warning(
                "Ollama model %r not available at %s. Run: ollama pull %s",
                config.ollama.model,
                config.ollama.url,
                config.ollama.model,
            )
        task = asyncio.create_task(poll_forever())
        warm = asyncio.create_task(warm_up())
        yield
        warm.cancel()
        task.cancel()
        await unload()
        await model.aclose()
        await calendar.aclose()

    app = FastAPI(title="Desk Secretary", lifespan=lifespan)

    # The desktop shell calls this just before it ends the app on Quit, because
    # that kill skips the normal shutdown (and so the unload above).
    @app.post("/api/unload")
    async def unload_endpoint() -> dict[str, bool]:
        await unload()
        return {"ok": True}

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        hub.clients.add(ws)
        await ws.send_text(json.dumps(await status()))
        await assistant.send_briefs()
        try:
            while True:
                msg = json.loads(await ws.receive_text())
                kind = msg.get("type")
                if kind == "start_brief":
                    assistant.start_brief(msg.get("id"))
                elif kind == "ask":
                    assistant.ask(str(msg.get("text", ""))[:2000])
                elif kind == "voice":
                    audio = _decode_audio(msg.get("audio"))
                    if audio:
                        assistant.voice(audio)
                elif kind == "stop":
                    assistant.stop()
        except WebSocketDisconnect:
            pass
        finally:
            hub.clients.discard(ws)

    app.mount("/", LocalStaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


def _decode_audio(data: Any) -> bytes | None:
    if not isinstance(data, str) or len(data) > MAX_VOICE_BYTES * 4 // 3 + 4:
        return None
    try:
        return base64.b64decode(data, validate=True) or None
    except (binascii.Error, ValueError):
        return None
