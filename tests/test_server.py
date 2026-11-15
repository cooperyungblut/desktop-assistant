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


def test_live_notes_over_the_websocket(tmp_path):
    from secretary.config import NotesConfig

    config = AppConfig(
        secretary_dir=tmp_path,
        state_file=tmp_path / "state.json",
        persona_file=ROOT / "personas" / "juniper.yaml",
        tts=TtsConfig(engine="none"),
        stt=SttConfig(engine="none"),
        notes=NotesConfig(dir=tmp_path / "notes"),
    )
    client = TestClient(create_app(config))
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "status"
        assert ws.receive_json() == {
            "type": "live_notes",
            "on": False,
            "hidden": False,
            "page": None,
            "lines": 0,
        }
        ws.send_json({"type": "speaking", "on": True, "text": "Hello."})  # no session: ignored
        ws.send_json({"type": "live_notes", "action": "start"})
        # Whisper is off here, so she says why it can't start.
        while (e := ws.receive_json())["type"] != "reply_end":
            pass
        assert e["text"].startswith("I couldn't start live notes:")
