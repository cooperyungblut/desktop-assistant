import json
import logging

import httpx

from secretary.config import OllamaConfig
from secretary.llm import OllamaClient, log_timings


def _client(handler):
    cfg = OllamaConfig()
    http = httpx.AsyncClient(base_url=cfg.url, transport=httpx.MockTransport(handler))
    return OllamaClient(cfg, client=http)


async def test_warm_up_sends_chat_options_so_ollama_does_not_reload():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"done": True})

    await _client(handler).warm_up({"num_ctx": 8192, "temperature": 0.7})
    assert seen[0]["options"]["num_ctx"] == 8192


async def test_stream_chat_disables_thinking_and_reports_it(caplog):
    seen = []
    lines = [
        {"message": {"thinking": "hmm let me see"}, "done": False},
        {"message": {"content": "Ten o'clock."}, "done": False},
        {"message": {"content": ""}, "done": True, "eval_count": 4, "eval_duration": 2e8},
    ]

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, text="\n".join(json.dumps(x) for x in lines))

    with caplog.at_level(logging.INFO, logger="secretary.llm"):
        out = [t async for t in _client(handler).stream_chat([], {})]
    assert "".join(out) == "Ten o'clock."
    assert seen[0]["think"] is False
    assert "14 hidden characters" in caplog.text


def test_log_timings_breaks_down_the_wait():
    line = log_timings(
        {
            "load_duration": 6.2e9,
            "prompt_eval_count": 1800,
            "prompt_eval_duration": 1.5e9,
            "eval_count": 60,
            "eval_duration": 1.2e9,
        }
    )
    assert line == "Ollama: load 6.2 s, prompt 1800 tok in 1.5 s, gen 60 tok in 1.2 s (50 tok/s)"
