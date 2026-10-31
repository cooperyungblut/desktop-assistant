"""Streaming chat client for Ollama's /api/chat endpoint.

Uses httpx directly instead of the `ollama` package: it's one endpoint, and
seeing the raw protocol (newline-delimited JSON) makes failures easier to debug.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Sequence
from typing import Any, Protocol

import httpx

from .config import OllamaConfig


class ChatModel(Protocol):
    def stream_chat(
        self, messages: Sequence[dict[str, str]], options: dict[str, Any]
    ) -> AsyncIterator[str]: ...


class LlmUnavailable(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, config: OllamaConfig, client: httpx.AsyncClient | None = None):
        self._config = config
        self._client = client or httpx.AsyncClient(
            base_url=config.url, timeout=httpx.Timeout(config.timeout_seconds, connect=5.0)
        )

    async def is_available(self) -> bool:
        try:
            r = await self._client.get("/api/tags")
            r.raise_for_status()
        except httpx.HTTPError:
            return False
        names = {m.get("name") for m in r.json().get("models", [])}
        return self._config.model in names or f"{self._config.model}:latest" in names

    async def stream_chat(
        self, messages: Sequence[dict[str, str]], options: dict[str, Any]
    ) -> AsyncIterator[str]:
        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": list(messages),
            "options": options,
            "stream": True,
            "keep_alive": self._config.keep_alive,
        }
        if self._config.think is not None:
            payload["think"] = self._config.think
        try:
            async with self._client.stream("POST", "/api/chat", json=payload) as r:
                if r.status_code != 200:
                    body = (await r.aread()).decode(errors="replace")
                    raise LlmUnavailable(f"Ollama returned {r.status_code}: {body[:300]}")
                filt = ThinkFilter()
                async for line in r.aiter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    if "error" in chunk:
                        raise LlmUnavailable(chunk["error"])
                    text = filt.feed(chunk.get("message", {}).get("content", ""))
                    if text:
                        yield text
                    if chunk.get("done"):
                        break
        except httpx.HTTPError as exc:
            raise LlmUnavailable(f"can't reach Ollama at {self._config.url}: {exc}") from exc

    async def aclose(self) -> None:
        await self._client.aclose()


class ThinkFilter:
    """Drop <think>...</think> blocks if a reasoning model emits them inline."""

    _OPEN, _CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self._buf = ""
        self._inside = False

    def feed(self, text: str) -> str:
        self._buf += text
        out = []
        while True:
            if self._inside:
                end = self._buf.find(self._CLOSE)
                if end < 0:
                    self._buf = self._buf[-len(self._CLOSE) :]
                    break
                self._buf = self._buf[end + len(self._CLOSE) :].lstrip()
                self._inside = False
            else:
                start = self._buf.find(self._OPEN)
                if start >= 0:
                    out.append(self._buf[:start])
                    self._buf = self._buf[start + len(self._OPEN) :]
                    self._inside = True
                    continue
                # Hold back a possible partial "<think" at the end of the chunk.
                keep = _partial_suffix(self._buf, self._OPEN)
                out.append(self._buf[: len(self._buf) - keep])
                self._buf = self._buf[len(self._buf) - keep :]
                break
        return "".join(out)


def _partial_suffix(text: str, tag: str) -> int:
    for n in range(min(len(tag) - 1, len(text)), 0, -1):
        if text.endswith(tag[:n]):
            return n
    return 0


_SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]*\s+")


class SentenceSplitter:
    """Turn a token stream into whole sentences so speech can start early."""

    def __init__(self, min_chars: int = 20) -> None:
        self._buf = ""
        self._min = min_chars

    def feed(self, text: str) -> list[str]:
        self._buf += text
        out = []
        start = 0
        for m in _SENTENCE_END.finditer(self._buf):
            if m.end() - start >= self._min:
                out.append(self._buf[start : m.end()].strip())
                start = m.end()
        self._buf = self._buf[start:]
        return [s for s in out if s]

    def flush(self) -> list[str]:
        rest, self._buf = self._buf.strip(), ""
        return [rest] if rest else []


def split_sentences(text: str) -> list[str]:
    s = SentenceSplitter()
    return s.feed(text) + s.flush()
