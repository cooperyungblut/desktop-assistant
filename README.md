# Desk Secretary

[![CI](https://github.com/cooperyungblut/desktop-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/cooperyungblut/desktop-assistant/actions/workflows/ci.yml)

A local AI secretary with a 3D avatar and a voice. Three times a day, a
scheduled Claude task reads my email and calendar and writes a short brief
into a synced Google Drive folder. This app picks the brief up, shows a
**brief ready** indicator, and when I click, a VRM avatar reads it aloud with
lip sync. Then I can ask follow-up questions, which a local LLM answers in
character.

Everything on the PC runs locally: no cloud calls and no running costs.

> 📽️ _Demo GIF goes here (recorded with fake sample briefs)._

## Features

- **Brief pickup**: polls the synced folder, validates each file against a
  versioned format, and never starts talking on its own.
- **Readout**: Kokoro text-to-speech, streamed sentence by sentence, with
  amplitude-based lip sync on a VRM avatar (three.js + three-vrm).
- **Follow-ups**: a local model through Ollama, with the brief as context and
  the session's conversation history, streamed token by token.
- **Persona as config**: name, traits, speaking style, answer length, example
  lines, sampling settings and voice all live in a YAML file.
- **Accuracy over personality**: the readout is read verbatim (it never passes
  through the local model), and fixed rules in code stop any persona from
  changing facts.
- **Expressions**: the model opens each reply with a mood tag (`[happy]`,
  `[sad]`, …) that the app strips from the text and speech and turns into an
  eased VRM facial expression. The moods are fixed in code; how each one looks
  is set per persona.
- **Push-to-talk**: hold V to ask out loud. faster-whisper transcribes on the
  CPU, and talking over the avatar interrupts her.
- **Escalation**: `/claude <question>` (or "check with Claude…") saves the
  question to a file that the next scheduled brief answers.

## Architecture

```mermaid
flowchart LR
  subgraph Cloud["claude.ai (already set up)"]
    T["Scheduled Claude tasks<br/>9:00 · 1:30 · 6:00"]
  end
  subgraph Drive["Google Drive folder (synced)"]
    B[("briefs/*.md")]
    E[("escalations/*.md")]
  end
  subgraph PC["Windows PC (this repo)"]
    W["BriefScanner<br/>(polls every 5 s)"]
    A["Assistant<br/>conversation + persona"]
    P["prompt.py<br/>rules + persona + brief"]
    O["Ollama<br/>local LLM (GPU)"]
    K["Kokoro TTS<br/>(ONNX, CPU)"]
    WH["faster-whisper STT<br/>(CPU)"]
    S["FastAPI<br/>WebSocket /ws"]
    UI["Browser UI<br/>three.js + VRM avatar<br/>lip sync · chat"]
  end
  T -- writes --> B
  T -- reads open questions --> E
  B --> W --> A
  A --> P --> O --> A
  A --> K --> A
  UI -- push-to-talk audio --> S --> WH --> A
  A -- writes --> E
  A <--> S <--> UI
```

| Piece | Where | Why it's built this way |
|-------|-------|-------------------------|
| File contract | [`docs/formats.md`](docs/formats.md) | Markdown + YAML front matter is easy for an LLM to write and for a person to read. One writer per file avoids Drive sync conflicts. |
| Folder watcher | `watcher.py` | Polling, not file-system events: Drive for Desktop doesn't reliably raise them, and briefs arrive three times a day. Half-synced files fail validation and are retried. |
| Prompt assembly | `prompt.py` | Rules live in code, not the persona file. A short persona reminder is re-injected before every question, because small models drift. Brief content is wrapped as untrusted data. |
| Assistant | `assistant.py` | Transport-agnostic. It emits events through a callback, so tests collect them in a list and the server forwards them over a WebSocket. |
| LLM client | `llm.py` | Raw `httpx` streaming against `/api/chat`. The token stream is cut into sentences so speech starts before the reply finishes. |
| STT | `stt.py` | faster-whisper on CPU: the GPU is reserved for the LLM. Audio is decoded with PyAV directly. A VAD filter plus a tiny deny-list handles Whisper's "silence hallucinations". |
| TTS | `tts.py` | `kokoro-onnx` on CPU: no PyTorch install, faster than real time, and it leaves VRAM for the LLM. |
| Front end | `web/` | Plain ES modules with an import map: no Node, no bundler. Markdown from briefs is sanitized with DOMPurify, since it contains email text. |

## Quick start (Windows, PowerShell)

Prerequisites: Python 3.11 or 3.12, [uv](https://docs.astral.sh/uv/),
[Ollama](https://ollama.com).

```powershell
git clone https://github.com/cooperyungblut/desktop-assistant.git
cd desktop-assistant
uv sync --extra tts --extra stt     # Python deps, including Kokoro and Whisper
uv run python scripts/download_models.py   # Kokoro model files (~340 MB)
ollama pull gemma4:12b              # or any chat model; set it in config.yaml
Copy-Item config.example.yaml config.yaml
# Save a VRM avatar as web/models/avatar.vrm (see web/models/README.md)
uv run python scripts/drop_sample.py       # fake brief into data/inbox
uv run desk-secretary               # opens http://127.0.0.1:8765
```

Click **Morning brief ready** (or press Space). Type a follow-up question or
hold **V** and ask it out loud. Start with `/claude` (or say "check with
Claude…") to save it for the next brief.

To use the real synced folder, set `secretary_dir` in `config.yaml` (which is
git-ignored) to the Drive folder that contains `briefs/`.

## Customizing the persona

Edit [`personas/juniper.yaml`](personas/juniper.yaml), or copy it and point
`persona_file` at the copy. Voices are listed in [`docs/voices.md`](docs/voices.md).

## Development

```powershell
uv run pytest           # tests (no models or GPU needed)
uv run ruff check .     # lint
uv run ruff format .    # format
```

CI runs lint and tests on Windows and Linux, plus a privacy check that fails
if anything that looks like a real brief, escalation, config file or avatar is
committed.

## Privacy

Real briefs contain email and calendar data. They stay in the synced folder,
which lives outside the repo. `config.yaml`, `data/`, `models/` and avatar
files are git-ignored. Everything under `samples/` is invented.

## Roadmap

See [`docs/plan.md`](docs/plan.md): a model-accuracy eval script, a
transparent always-on-top desktop window, a wake word, and allow-listed actions.

## AI assistance

This project was built with [Claude](https://claude.ai) (Anthropic) as a
coding partner. Claude wrote most of the code, including the core logic, from
my requirements and architecture decisions. I reviewed, ran and tested it.
The briefs themselves are produced by Claude scheduled tasks in claude.ai.

## License

MIT
