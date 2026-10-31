from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

import pytest

from secretary.persona import load_persona

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "samples" / "briefs"


@pytest.fixture
def persona():
    return load_persona(ROOT / "personas" / "juniper.yaml")


@pytest.fixture
def morning_text() -> str:
    return (SAMPLES / "2026-11-02_morning.md").read_text(encoding="utf-8")


class FakeModel:
    """Stands in for Ollama: records requests, streams a canned reply."""

    def __init__(self, reply: str = "It's at ten. Have fun!"):
        self.reply = reply
        self.requests: list[list[dict[str, str]]] = []

    async def stream_chat(
        self, messages: Sequence[dict[str, str]], options: dict[str, Any]
    ) -> AsyncIterator[str]:
        self.requests.append(list(messages))
        for word in self.reply.split(" "):
            yield word + " "


@pytest.fixture
def fake_model():
    return FakeModel()
