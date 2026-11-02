# Kokoro voices

Set `voice.kokoro_voice` in the persona file. A few American English voices
that suit a bright, friendly persona (prefix `af_` = American female,
`am_` = American male, `bf_`/`bm_` = British):

| Voice       | Character                            |
|-------------|--------------------------------------|
| `af_heart`  | Warm and expressive (the default)    |
| `af_bella`  | Bright, energetic                    |
| `af_nicole` | Soft, close-mic                      |
| `af_sky`    | Light and young                      |
| `bf_emma`   | British, clear                       |
| `am_michael`| American male, steady                |

The full list is in `voices-v1.0.bin`. List it with:

```powershell
uv run python -c "from kokoro_onnx import Kokoro; print(Kokoro('models/kokoro-v1.0.onnx','models/voices-v1.0.bin').get_voices())"
```

`voice.speed` is 0.5 to 2.0; 1.0 to 1.1 sounds natural.

## Pauses between sentences

The app speaks one sentence at a time, so speech can start before the whole
reply exists. Kokoro trims the silence off each clip, which on its own leaves
only about 0.1 s between sentences and sounds rushed. So each clip gets a set
pause at its end, in the persona file:

```yaml
voice:
  sentence_pause: 0.35   # after . ! ?
  clause_pause: 0.15     # after , ; : or a dash
  split_clauses: true
```

Commas need the same treatment. Left to itself, Kokoro's pause at a comma is
anywhere from 0.05 s to 0.4 s, often as long as a full stop, which is what
made her sound halting. With `split_clauses` each clause is synthesized on
its own and joined with `clause_pause`. Very short pieces ("Good morning,
Cooper.") stay in one piece, and commas inside numbers never split.

To compare pauses, `try_voices.py` takes several values and writes one file
per value, spoken sentence by sentence exactly like the app:

```powershell
uv run python scripts/try_voices.py af_heart --pause 0 0.25 0.35 0.5
```

`--pause 0` is how the app sounded before this setting existed. For commas,
`--comma off 0.1 0.15 0.2` does the same (`off` is Kokoro's own pauses).

## Blending voices

`voice.kokoro_voice` can also be a blend: `"af_heart:70,af_bella:30"`. The app
averages the voices' style vectors by weight (weights are relative, so 70/30
and 7/3 are the same). Quote it in YAML because of the colon.

In practice blends can blur some pronunciations: averaging style vectors
mixes how the voices say certain sounds, not just their timbre. Juniper
tried an 80/20 heart/nicole blend and went back to plain `af_heart`, so
listen to longer sentences with names and numbers before settling on one.

To audition voices and blends before choosing, save samples as WAV files:

```powershell
uv run python scripts/try_voices.py af_heart af_bella "af_heart:70,af_bella:30"
```

They land in `data/voice-samples/`. Pass `--text "..."` to hear your own line.
