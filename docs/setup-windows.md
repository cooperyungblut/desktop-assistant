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
   **Check:** `37 passed`.
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
2. Update the three scheduled tasks with the instructions at the end of
   `docs/formats.md`, and paste the sample brief in as the example.
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
