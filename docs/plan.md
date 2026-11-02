# Plan

Work is split into chunks of about two to three hours. Each milestone ends with
something that runs, plus a short "how it works" note in `docs/interview-notes.md`.

## Model choice and VRAM budget (RTX 4070 Ti Super, 16 GB)

| Component                                  | Where | VRAM, approx. |
|--------------------------------------------|-------|---------------|
| Chat model, 12B at Q4 + 8K context          | GPU   | 8–9 GB        |
| faster-whisper `small.en` int8 (Milestone 2) | GPU  | ~1 GB         |
| Kokoro 82M (ONNX)                           | CPU   | 0             |
| Browser + three.js avatar                   | GPU   | 0.5–1 GB      |
| Windows desktop, other apps                 | GPU   | 1–2 GB        |
| **Total**                                   |       | **~11–13 GB** |

**Recommendation: Gemma 4 12B** (`gemma4:12b`; check the exact tag on
ollama.com/library). The Gemma family is strong at warm, natural conversation,
follows a system prompt well, and leaves headroom at Q4. Alternatives worth
trying, since switching is one line in `config.yaml`:

- **Qwen3.5 9B** (or Qwen3 14B): stronger at strict instruction following.
  These are reasoning models. Keep `think: false` (the default) or every answer
  starts with seconds of hidden "thinking". Gemma 4 can think too.
- **Ministral 3 14B**: good conversational tone, about 8 GB at Q4.

Avoid 20B+ models here. They fit alone but crowd out Whisper and the avatar.

Pick by testing, not by leaderboard: Milestone 3 adds a small script that asks
each model the same questions about the sample briefs and checks that the
facts survive.

## Milestone 1: weekend MVP ✅

A fake brief lands in the folder, the indicator shows, I click, the avatar
reads it aloud with lip sync, and I can type follow-up questions answered in
persona.

- [x] File formats: brief, escalation, handled-state (`docs/formats.md`)
- [x] Brief scanner with retry for half-synced files
- [x] Persona file + prompt assembly with per-turn reminder
- [x] Ollama streaming client, sentence splitter
- [x] Kokoro TTS, sentence-by-sentence streaming
- [x] Escalation writer + "answered" marking
- [x] Web UI: VRM avatar, blink/idle, amplitude lip sync, chat, brief panel
- [x] Tests + CI on Windows and Linux + privacy check
- [x] Run it on Windows (setup guide chunks A and B)
- [x] Scheduled tasks write the new format; real briefs sync and parse
- [ ] Record a demo GIF with sample data

## Milestone 2: push-to-talk voice input ✅ (code complete)

- [x] Hold **V** (or the mic button) to record in the browser with `MediaRecorder`;
      the clip goes over the WebSocket as base64 WebM/Opus
- [x] Decode with PyAV to 16 kHz mono, transcribe with faster-whisper `small.en`
      (CPU int8 by default, CUDA optional with automatic CPU fallback)
- [x] VAD filter + a small filter for Whisper's "silence hallucinations"
- [x] The transcript goes through the same path as a typed question, so
      "check with Claude…" escalates by voice too
- [x] Talking over her interrupts her (barge-in)
- [x] Tests with a fake Whisper model and real PyAV-encoded Opus; an end-to-end
      run in headless Chromium with a fake microphone
- [ ] **Cooper:** run setup Chunk D on Windows

Decision: Whisper runs on the **CPU** by default. The chat model already uses
most of the GPU (13.6 of 16.4 GB in use during testing), CUDA on Windows needs
an extra ~1 GB of cuBLAS/cuDNN DLLs, and `small.en` on a desktop CPU handles a
short question in about a second. `device: cuda` is one config line away.

## Milestone 3: expressions + model eval ✅

Chunk 1 (expressions, plus two tuning items from using Milestone 2):

- [x] The model starts each reply with a tag such as `[happy]`, from a fixed set
  (`neutral, happy, surprised, sad, relaxed`). The app strips it before display
  and speech, validates it against the set, and eases the VRM expression in
  and out. Unknown tags fall back to neutral.
- [x] Persona file gets an `expressions:` map (emotion → VRM preset + weight).
- [x] Persona: answer small talk warmly in a few words before the facts.
- [x] Speech: a set pause after each sentence (`voice.sentence_pause`), since
  per-sentence clips left only ~0.1 s between sentences, and after each comma
  (`voice.clause_pause`), since Kokoro's own comma pauses ranged 0.05–0.4 s.
- [x] **Cooper:** Chunk E in the setup guide (try it with the real avatar,
  pick a pause, tune expression weights).

Chunk 2:

- [x] `scripts/eval_models.py`: runs fact-check questions (`evals/cases.yaml`)
  against each candidate model and reports which facts were dropped or
  changed, whether it admits what the brief doesn't say, mood-tag use, and
  speed.
- [x] **Cooper:** Chunk F in the setup guide (run it on gemma4:12b and one
  or two alternatives). gemma4:12b scored 100% on every check over 3 runs,
  0.4 s to the first word. The eval is at its ceiling, so it needs harder
  cases before it can rank models.

## Milestone 4: desktop presence (~2 chunks, spike first)

Frameless, transparent, always-on-top window with the avatar on the desktop.

- **Spike first (1 hour):** test transparency with the actual three.js canvas
  on Windows before committing.
- **Recommendation: Electron.** Transparent, frameless, always-on-top windows
  and click-through (`setIgnoreMouseEvents`) work reliably on Windows. It's
  heavy, but it's the boring, well-trodden path.
- **Tauri** is lighter and also supports transparency on Windows (WebView2),
  but it adds a Rust toolchain and has more edge cases with click-through.
- **pywebview** would keep everything in Python, but transparent windows on
  Windows have historically been poorly supported. Test it in the spike, but
  don't plan on it.
- The UI is already a plain web page with a transparent WebGL canvas
  (`alpha: true`), so any of the three just loads `http://127.0.0.1:8765`.
- Global hotkey moves into the desktop shell at this point.

Spike (chunk 1):

- [x] `desktop/`: Electron window, frameless, transparent, always-on-top,
  loading the app's page with `?desktop` (avatar, caption and brief button only)
- [x] Renders on the GPU (Electron's default; the startup log shows the GPU
  status) and draws at most 30 fps while she's idle, full rate while talking
- [x] Click-through except over her buttons (`setIgnoreMouseEvents` with
  forwarded mouse moves); move mode to drag her; size presets; position saved
- [x] Global hotkeys: Ctrl+Alt+Space talk, Ctrl+Alt+B brief, Ctrl+Alt+M move,
  Ctrl+Alt+H hide; tray menu with the same plus Quit
- [x] **Cooper:** Chunk G in the setup guide. Clean transparency, WebGL on
  the NVIDIA GPU, about 1.4% CPU while idle, click-through works. Another app
  held Ctrl+Alt+Space/B/M, so hotkeys now fall back to free ones (PR #18).

Chunk 2:

- [x] Chat panel inside her window (hotkey or tray): the brief, the chat and
  the text box open to her left. One window, because every connected page
  plays her speech. The panel goes left or right of her (tray menu); the window
  always keeps room for it, because resizing a window on Windows briefly
  shows the old frame at the new position.
- [x] "Read me this morning's brief" (typed or spoken) reads that brief
  verbatim; matched in code like `/claude`, never decided by the model
- [x] One launch: `npm start` starts the Python app if it isn't running and
  stops it on Quit
- [ ] **Cooper:** Chunk H in the setup guide
- [ ] Later: hold-to-talk from any app (needs a native keyboard hook;
  Electron's global shortcuts only see presses, not releases)

## Milestone 5: wake word (~1–2 chunks)

- openWakeWord (runs on CPU, custom "hey Juniper" model trainable from
  synthetic speech) feeding into the Milestone 2 pipeline.
- Must be off by default and show a clear "listening" state.

## Milestone 6: allow-listed actions (~2 chunks)

- The model may only *request* actions from a fixed list: `open_link` (only
  URLs that appear in the brief), `open_app` (names mapped to paths in local
  config), `focus_window`, `set_timer`.
- Requests are structured (JSON), validated in code against the list and its
  arguments, and shown as a confirm card. Nothing runs without a click.
- Brief content is untrusted: an email can't trigger an action. Only a request
  that follows a message *I* typed or said can create one, and the link must
  match a link in the brief exactly.

## Later options

- Direct Claude API escalation with a hard monthly spending cap.
- Custom VRoid Studio avatar.
- Package as a single installer.
