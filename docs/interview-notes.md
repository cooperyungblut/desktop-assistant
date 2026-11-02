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

## Milestone 2: push-to-talk voice input

**What it does.** Hold V (or a mic button) to talk. Releasing it sends the
clip to the backend, Whisper transcribes it, the text appears as your chat
bubble, and from there it is exactly a typed question.

**The flow.**
1. `PushToTalk` (web/js/mic.js) opens the mic once and keeps it open, so later
   presses start instantly. It records with `MediaRecorder` while the key is
   held, plus 200 ms after release so the last word isn't clipped. Taps under
   300 ms are ignored.
2. The clip goes over the existing WebSocket as base64 (a few seconds of
   Opus is ~30 KB, so a separate upload endpoint isn't worth it).
3. `decode_audio` uses PyAV to turn WebM/Opus into 16 kHz mono float32, and
   faster-whisper transcribes it in a worker thread with a VAD filter.
4. `Assistant._voice` emits the transcript, then calls the same `_answer`
   used for typed questions, inside the same cancellable task.

**Key decisions.**
- *Reuse one path.* Voice is just another way to produce text. Escalation,
  persona, history and streaming speech all work unchanged, and they're
  already tested.
- *CPU, not GPU, for Whisper.* A resource-budget call: the LLM needs the VRAM,
  CUDA on Windows adds ~1 GB of DLLs, and the CPU is fast enough for short
  clips. The GPU is a config option with an automatic fallback, and the
  fallback is tested.
- *Barge-in.* Pressing to talk stops her speech and cancels the in-flight
  answer, because that's how a conversation works.
- *Whisper hallucinations.* Whisper was trained on subtitles, so silence can
  come out as "Thanks for watching!". The VAD filter removes most of it, and a
  tiny deny-list catches the rest without throwing away real phrases like
  "thank you".

- *Context biasing instead of a bigger model.* `base.en` heard "Northwind
  Traders" as "Northwind Traitors". Rather than move Whisper to the GPU, the app
  pulls proper nouns out of the current brief (`vocabulary.py`) and passes them
  as Whisper's `initial_prompt`, which biases it toward those spellings. It
  costs nothing at runtime and uses context the app already has.

**A bug the end-to-end test caught.** Unit tests with a fake Whisper model
passed, but running the real page in headless Chromium with a fake
microphone showed faster-whisper's own audio decoder crashing against the
current PyAV release (it passes an option PyAV removed). The fix was to decode
with PyAV directly, which also removed a dependency on library internals. The
test suite now encodes real Opus audio with PyAV, so this can't regress
silently. Mocks prove your logic; only a real run proves your integration.

**A latency bug that wasn't the GPU.** After voice input worked, the first
word of each reply took 7 to 35 seconds. The obvious suspects were VRAM
pressure and Whisper competing with the LLM. Logging Ollama's own timing
breakdown (load, prompt, generation) showed all three were fast: Gemma 4 was
"thinking" silently before every answer, because reasoning is on by default
when the request doesn't say otherwise. Sending `think: false` brought the
first token down to about 0.6 s. Measure before you optimize: the timing log
stays in, and it warns if a model spends time thinking.

## Milestone 3: facial expressions

**What it does.** The model starts each reply with a mood tag such as
`[happy]` or `[sad]`. The app pulls the tag out of the token stream before
anything is shown or spoken, and the avatar's face eases into the matching
VRM expression, holds it while she talks, and relaxes after she stops.

**How it works.**
1. A fixed rule in the system prompt (in code, like the accuracy rules) asks
   for one tag from a fixed set, and the per-turn reminder repeats it.
2. `TagFilter` sits between the LLM stream and everything else. Tokens arrive
   in pieces (`[`, `sur`, `prised`, `]`), so it holds back a possible tag
   until it can decide, then releases the text and reports the mood.
3. The server maps mood → VRM preset + weight from the persona file and sends
   an `expression` event. The browser eases each preset's weight toward its
   target every frame (faster in than out).

**Key decisions.**
- *The model chooses the mood; code owns the vocabulary.* The set of moods is
  fixed in code and validated, so the persona file can change how "sad" looks
  but can't invent a mood the UI doesn't know. Unknown or missing tags fall
  back to neutral rather than failing.
- *Forgiving parser.* Small models don't follow formats perfectly. An unknown
  tag at the start still counts as a tag, a known tag mid-reply changes the
  face, and ordinary brackets in text are left alone. Tags never reach the
  speech engine, which would read them aloud.
- *Tags stay in the history.* The model sees its own earlier replies with
  their tags, which makes it more likely to keep using the format.
- *Tuned on the real avatar.* On Cooper's avatar the built-in "happy" preset
  squints the eyes and opens the mouth. At any strength it looked either
  odd or not happy at all, and with lip sync opening the mouth again on top it
  looked alarming. Three fixes: mood presets scale the lip-sync mouth down by
  their own weight (VRM's "blend" mouth override); each face drops to half
  strength while she talks; and a mood can be a mix of presets, so "happy" is
  now mostly "relaxed" with a touch of "happy". The face also holds through
  the gaps between sentences: the server sends `speech_done` when a reply's
  speech is finished, and only then does the face relax.
- *Readouts stay neutral.* The brief readout is read verbatim and never goes
  through the model, so there's no mood to pick. That's on purpose: the face
  shouldn't editorialize the facts.

**Speech pauses.** Speech is made sentence by sentence so she can start
talking before the reply is finished. Measuring the clips showed Kokoro trims
each one, leaving about 0.1 s of silence between sentences, which sounded
rushed. Each clip now ends with a set pause (counting the silence it already
has), configurable per persona. Listening showed the bigger problem was at
commas: Kokoro's comma pauses ranged from 0.05 s to 0.4 s within a single
reply. The fix synthesizes each clause separately and joins them with a fixed
short pause, keeping very short pieces together so they don't sound clipped.
