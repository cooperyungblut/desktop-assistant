"""Save a short sample of each voice (or blend) as a WAV file so you can compare them.

    uv run python scripts/try_voices.py af_heart af_bella "af_heart:70,af_bella:30"
    uv run python scripts/try_voices.py "af_heart:50,bf_emma:50" --text "Hi Cooper!"

A blend is "name:weight,name:weight"; weights are relative (70/30 = 0.7/0.3).
Files go to data/voice-samples/ (git-ignored). Put the one you like in your
persona file as voice.kokoro_voice, exactly as you typed it here.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from secretary.config import TtsConfig
from secretary.tts import resolve_voice, to_wav

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEXT = (
    "Good morning, Cooper! You have a phone screen request from Contoso Data, "
    "and your coffee chat with Sam is at ten thirty. Want me to read the rest?"
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "voices", nargs="+", help='voice names or blends like "af_heart:70,af_bella:30"'
    )
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--speed", type=float, default=1.05)
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "voice-samples")
    args = parser.parse_args()

    from kokoro_onnx import Kokoro

    cfg = TtsConfig()
    kokoro = Kokoro(str(ROOT / cfg.model_path), str(ROOT / cfg.voices_path))
    args.out.mkdir(parents=True, exist_ok=True)
    for spec in args.voices:
        samples, rate = kokoro.create(
            args.text, voice=resolve_voice(kokoro, spec), speed=args.speed, lang="en-us"
        )
        name = re.sub(r"[^A-Za-z0-9]+", "_", spec).strip("_")
        path = args.out / f"{name}.wav"
        path.write_bytes(to_wav(samples, rate))
        print(f"{spec:<30} -> {path}")


if __name__ == "__main__":
    main()
