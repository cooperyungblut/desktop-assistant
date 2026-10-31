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
