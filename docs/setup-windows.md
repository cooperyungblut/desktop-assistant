# Windows setup, step by step

Each chunk ends with a **Check**. If a check fails, stop there and paste the
error. Run everything in PowerShell from the repo folder.

## Chunk A: text-only end to end (about 45 min)

1. Install uv (skip if `uv --version` works):
   ```powershell
   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
   ```
   Close and reopen PowerShell afterwards.
2. Get the code and install:
   ```powershell
   cd $HOME\source\repos          # or wherever you keep code
   git clone https://github.com/cooperyungblut/desktop-assistant.git
   cd desktop-assistant
   uv python install 3.12
   uv sync --python 3.12
   uv run pytest -q
   ```
   **Check:** the last line says `passed` with no failures.
3. Config and a fake brief:
   ```powershell
   Copy-Item config.example.yaml config.yaml
   uv run python scripts/drop_sample.py
   ```
   **Check:** it prints `Dropped ...\data\inbox\briefs\<today>_morning.md`.
4. Avatar: download a VRM from VRoid Hub (see `web/models/README.md`) and save
   it as `web\models\avatar.vrm`.
5. Run, text-only for now:
   ```powershell
   uv run desk-secretary
   ```
   **Check:** the browser opens, the avatar stands with arms down and blinks,
   and a purple **Morning brief ready** button pulses. The "voice" dot is red
   (expected: no TTS yet). Click the button: the brief shows on the right,
   captions appear and the mouth moves. The "model" dot is red until Chunk B.
   Typing a question replies "my brain isn't answering", which is also expected.
   Type `/claude is the screen technical?`.
   **Check:** a file appears in `data\inbox\escalations\`. Open it and check the format.

## Chunk B: voice and brain (about 1 hour, mostly downloads)

1. Kokoro:
   ```powershell
   uv sync --extra tts
   uv run python scripts/download_models.py
   ```
2. Ollama (install from ollama.com if needed), then:
   ```powershell
   ollama pull gemma4:12b
   ollama run gemma4:12b "Say hi in five words."
   ```
   If the tag doesn't exist, look up the current Gemma tag on
   ollama.com/library and put it in `config.yaml` under `ollama.model`.
3. Reset the sample so it shows as new, and run:
   ```powershell
   Remove-Item data\state.json
   uv run desk-secretary
   ```
   **Check:** all three dots are green. Clicking the brief speaks it aloud with
   lip sync. Ask "when is my coffee chat?" and "what did Northwind say?".
   The answers should be correct, short and in character. The Northwind
   rejection must stay a rejection.
4. While it's talking, open a second PowerShell and run `nvidia-smi`.
   **Check:** total GPU memory used is under about 12 GB.

## Chunk C: hook up the real folder (about 30 min)

1. In Google Drive, create `Secretary/briefs` and `Secretary/escalations`.
2. Update the scheduled tasks with the blocks in
   `docs/scheduled-task-prompts.md`, then use **Run now** on one of them.
3. In `config.yaml`, set `secretary_dir` to the synced folder, e.g.
   `secretary_dir: "G:/My Drive/Secretary"`.
4. Drop a sample brief into the real folder to test the sync path:
   ```powershell
   uv run python scripts/drop_sample.py --inbox "G:/My Drive/Secretary"
   ```
   **Check:** the indicator appears within about 5 seconds. Then delete that
   sample from Drive.
5. After the next real scheduled run: **Check** that the brief file is a real
   `.md` (not `.gdoc`) and that the app picks it up. If it shows a warning in
   the terminal, the message says what's wrong with the format.

## Chunk D: push-to-talk voice input (about 30 min)

1. Get the new code and the speech-recognition packages:
   ```powershell
   git checkout main
   git pull
   uv sync --extra tts --extra stt
   ```
   Always list both extras when you run `uv sync`. Leaving one out uninstalls it.
2. Start the app: `uv run desk-secretary`. The first start downloads the
   Whisper `small.en` model (about 480 MB) into `models\whisper`.
   **Check:** the terminal prints `Whisper small.en ready on cpu (int8)`, and the
   **mic** dot in the top bar turns green. A warning about symlinks from
   Hugging Face is harmless.
3. Click in the page (not in the text box), then **hold V** and ask
   "When is my coffee chat?". Release when you're done talking. The first time,
   the browser asks for microphone access: choose **Allow**.
   **Check:** your words appear as a chat bubble, then she answers out loud.
   The mic button next to the text box is a toggle: click it, talk, then
   click it again or just pause for 3 seconds and it sends what you said.
4. While she's talking, hold V and start a new question.
   **Check:** she stops mid-sentence and answers the new one.
5. Look for `Transcribed ... in X s` in the terminal.
   **Check:** X is under about 1.5 s. If it's slower, try `model: base.en`
   under `stt:` in `config.yaml`, or the GPU option below.

Your `config.yaml` doesn't need an `stt:` section; the defaults are the ones in
`config.example.yaml`. Add the section only to change them.

## Chunk E: expressions and pauses (about 30 min)

1. Get the new code (Milestone 3, chunk 1):
   ```powershell
   git checkout main
   git pull
   uv sync --extra tts --extra stt
   ```
   (Add `--extra stt-gpu` too if you use Whisper on the GPU.)
2. Pick a sentence pause. Make samples, then play them from File Explorer:
   ```powershell
   uv run python scripts/try_voices.py af_heart --pause 0 0.25 0.35 0.5
   explorer data\voice-samples
   ```
   `pause0ms` is the old sound. Put the one you like in
   `personas\juniper.yaml` as `voice: sentence_pause:` (the default is 0.35).
   Do the same for commas with `--comma off 0.1 0.15 0.2` (`off` is the old
   sound) and set `clause_pause:`.
3. Start the app (`uv run desk-secretary`), open the page, and press **F12**
   to open the browser console. Type:
   ```js
   secretary.avatar.expressionNames()
   ```
   **Check:** the list includes `happy`, `sad` and `relaxed`. If `surprised`
   is missing (VRM 0.x models), set `surprised: { preset: happy, weight: 0.4 }`
   under `expressions:` in the persona file.
4. Try each face from the console, e.g. `secretary.avatar.setExpression("sad", 0.45)`,
   then `secretary.avatar.setExpression("neutral")`. To try a mix of presets:
   `secretary.avatar.setFaces({ relaxed: 0.5, happy: 0.15 })`.
   **Check:** the face eases in and back out. If one looks overdone, lower its
   `weight` in the persona file, or use a `mix:` (see the comments there), and
   restart the app (Ctrl+C, then start it again).
5. Ask a few questions out loud or typed: say hello, ask about a rejection in
   the brief, ask something good.
   **Check:** she answers a hello warmly in a few words before any facts, her
   face matches the news while she talks, then relaxes about 1.5 s after she
   stops. No `[happy]`-style tags appear in the chat or get read aloud.
6. If a tag ever shows up in the chat, or her face never changes, tell me
   which model you were on. Chunk 2's eval script will measure how reliably
   each model writes the tag.

### Optional: Whisper on the GPU

Faster (about 0.2 s per question) but uses about 1 GB more VRAM, which you're
short on while Wallpaper Engine and games are running.

```powershell
uv sync --extra tts --extra stt --extra stt-gpu   # ~1 GB of CUDA libraries
```

Then in `config.yaml`:

```yaml
stt:
  device: cuda
  compute_type: int8_float16
```

**Check:** the terminal says `ready on cuda`. If CUDA fails, the app logs why
and falls back to the CPU, so speech input keeps working either way.

## Chunk F: compare models (about 30 min, mostly waiting)

1. Get the new code: `git pull` (no new packages).
2. Run the eval on the model you use now. Ollama must be running; close the
   app first so the two don't compete for the GPU.
   ```powershell
   uv run python scripts/eval_models.py --runs 3 -v
   ```
   It asks 15 questions about the sample briefs, 3 times each, and prints a
   dot per passed answer and an `x` per failed one, then a table:
   - **facts**: answers with every required fact and nothing changed or invented
   - **unknown**: questions the brief can't answer where she admitted it
   - **tag** / **mood**: replies with a mood tag, and with one that fits
   - **first** / **total**: median seconds to the first word / whole reply

   With `-v` each failure shows the answer, so you can tell a real mistake
   from a check that's too strict. Send me those lines if a check looks wrong.
3. Optional: pull one or two alternatives and compare them in one run:
   ```powershell
   ollama pull qwen3.5:9b
   uv run python scripts/eval_models.py gemma4:12b qwen3.5:9b --runs 3
   ```
   Check the exact tags on ollama.com/library first. Each 8-12B model is a
   5-8 GB download. Results are also saved in `data\evals\`.
4. If another model wins clearly on facts and is still fast, switch
   `ollama: model:` in `config.yaml`.
