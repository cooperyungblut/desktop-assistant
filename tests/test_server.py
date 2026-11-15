from fastapi.testclient import TestClient

from secretary.config import AppConfig, SttConfig, TtsConfig
from secretary.server import create_app
from tests.conftest import ROOT


def test_web_files_are_revalidated_on_every_load(tmp_path):
    config = AppConfig(
        secretary_dir=tmp_path,
        state_file=tmp_path / "state.json",
        persona_file=ROOT / "personas" / "juniper.yaml",
        tts=TtsConfig(engine="none"),
        stt=SttConfig(engine="none"),
    )
    client = TestClient(create_app(config))  # no `with`: skip startup tasks
    for path in ("/", "/js/app.js"):
        r = client.get(path)
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-cache"


def test_unload_endpoint_asks_ollama_to_free_the_model_once(tmp_path, monkeypatch):
    calls = []

    async def fake_unload(self):
        calls.append(self)

    monkeypatch.setattr("secretary.llm.OllamaClient.unload", fake_unload)
    config = AppConfig(
        secretary_dir=tmp_path,
        state_file=tmp_path / "state.json",
        persona_file=ROOT / "personas" / "juniper.yaml",
        tts=TtsConfig(engine="none"),
        stt=SttConfig(engine="none"),
    )
    client = TestClient(create_app(config))
    assert client.post("/api/unload").json() == {"ok": True}
    assert client.post("/api/unload").json() == {"ok": True}
    assert len(calls) == 1


def test_ask_passes_pictures_through_and_reports_unreadable_ones(tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(
        "secretary.assistant.Assistant.ask",
        lambda self, text, pictures=(): asked.append((text, pictures)),
    )
    config = AppConfig(
        secretary_dir=tmp_path,
        state_file=tmp_path / "state.json",
        persona_file=ROOT / "personas" / "juniper.yaml",
        tts=TtsConfig(engine="none"),
        stt=SttConfig(engine="none"),
    )
    client = TestClient(create_app(config))
    png = "data:image/png;base64,iVBORw0KGgoAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    with client.websocket_connect("/ws") as ws:
        ws.receive_json()  # status
        ws.send_json({"type": "ask", "text": "what's this?", "images": [png]})
        ws.send_json({"type": "ask", "text": "and this?", "images": ["junk"]})
        while (e := ws.receive_json())["type"] != "error":
            pass
        assert e["message"] == "That picture couldn't be read."
    assert asked[0][0] == "what's this?"
    assert [p.mime for p in asked[0][1]] == ["image/png"]
    assert asked[1] == ("and this?", [])
