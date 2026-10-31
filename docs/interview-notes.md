# Interview notes

Short explanations of how each milestone works and why, in plain language.

## Milestone 1: the MVP

**What it does.** A Python service watches a synced folder for brief files
that Claude writes on a schedule. When one appears, a browser UI shows "brief
ready". On click, a 3D avatar reads the brief aloud with lip sync, then answers
typed follow-ups using a local LLM, in a configurable persona.

**The flow of one question.**
1. The browser sends `{"type": "ask"}` over a WebSocket.
2. `Assistant` builds the prompt: fixed rules, the persona, the brief wrapped in
   `<brief>` tags, recent history, then a short persona reminder right before
   the question.
3. `OllamaClient` streams tokens from `/api/chat`. Each token goes to the UI,
   and a `SentenceSplitter` collects whole sentences.
4. Each finished sentence goes into a queue. A second task turns it into speech
   with Kokoro and sends the WAV over the WebSocket, so speech starts after the
   first sentence instead of after the whole reply.
5. The browser plays the clips in order through an `AnalyserNode`. Loudness
   (RMS) drives the VRM's `aa` mouth shape, smoothed so the jaw doesn't jitter.

**Key decisions, and the trade-offs.**

- *A file contract between two systems.* The brain (Claude in the cloud) and the
  face (this app) only share a folder. The format is versioned (`schema:
  secretary-brief/1`) and validated with Pydantic, like an API contract. Each
  file has exactly one writer, so Google Drive never creates conflict copies.
  The app keeps its own "handled" state locally instead of editing Claude's files.
- *Polling, not file events.* Drive for Desktop's virtual drive doesn't reliably
  fire change events, and partial syncs are common. A 5-second poll is simple
  and testable, and briefs arrive only three times a day. A file that fails
  validation is retried next poll, which handles half-synced files for free.
- *Accuracy by construction.* The readout is the brief's spoken script, read
  verbatim. It never goes through the local model, so a 12B model can't garble
  the most important part. For follow-ups, the rules live in code (the persona
  file can't remove them), and a reminder is re-injected each turn because small
  models drift from instructions that sit far back in the context.
- *Prompt injection.* Brief text includes emails written by strangers. It's
  wrapped and labelled as data, rendered through DOMPurify in the UI, and (for
  the actions milestone) can never trigger an action by itself.
- *Escalation is deterministic.* A regex (`/claude …`, "check with Claude")
  decides, not the model. Small models over-trigger tool use, and writing a file
  that a paid system acts on should be explicit.
- *Testability.* `Assistant` doesn't know about WebSockets. It calls an `emit`
  callback, and the LLM and TTS are `Protocol`s. Tests swap in a fake model and a
  silent speaker and assert on the list of events, with no GPU or network.
- *Small dependency footprint.* httpx instead of an SDK for one endpoint.
  kokoro-onnx instead of the PyTorch package (no 2 GB torch install, runs on
  CPU, leaves VRAM for the LLM). No JS build step: an import map with pinned
  CDN versions.

**What I'd improve next.** Voice input (faster-whisper), emotion tags mapped to
VRM expressions, and an eval script that checks which local model keeps facts
straight.
