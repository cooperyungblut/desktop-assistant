"""Save a short sample of each voice (or blend) as a WAV file so you can compare them.

    uv run python scripts/try_voices.py af_heart af_bella "af_heart:70,af_bella:30"
    uv run python scripts/try_voices.py "af_heart:50,bf_emma:50" --text "Hi Cooper!"
    uv run python scripts/try_voices.py "af_heart:80,af_nicole:20" --pause 0.25 0.35 0.5
    uv run python scripts/try_voices.py "af_heart:80,af_nicole:20" --comma off 0.1 0.15 0.2

Like the app, the text is spoken one sentence at a time with --pause seconds of
silence after each sentence (voice.sentence_pause in the persona file) and
--comma seconds after each comma (voice.clause_pause; "off" lets Kokoro pick,
like voice.split_clauses: false). Give several values to get one file each.

A blend is "name:weight,name:weight"; weights are relative (70/30 = 0.7/0.3).
Files go to data/voice-samples/ (git-ignored). Put the one you like in your
persona file as voice.kokoro_voice, exactly as you typed it here.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import numpy as np

from secretary.config import TtsConfig
from secretary.llm import split_sentences
from secretary.persona import Voice
from secretary.tts import speak, to_wav

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
    parser.add_argument("--speed", type=float, default=1.1)
    parser.add_argument(
        "--pause",
        type=float,
        nargs="+",
        default=[Voice().sentence_pause],
        help="seconds of silence after each sentence (several values = several files)",
    )
    parser.add_argument(
        "--comma",
        nargs="+",
        default=[str(Voice().clause_pause)],
        help='seconds of silence after each comma, or "off" to let Kokoro choose',
    )
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "voice-samples")
    args = parser.parse_args()

    from kokoro_onnx import Kokoro

    cfg = TtsConfig()
    kokoro = Kokoro(str(ROOT / cfg.model_path), str(ROOT / cfg.voices_path))
    args.out.mkdir(parents=True, exist_ok=True)
    many = len(args.pause) > 1 or len(args.comma) > 1
    for spec in args.voices:
        name = re.sub(r"[^A-Za-z0-9]+", "_", spec).strip("_")
        for pause in args.pause:
            for comma in args.comma:
                split = comma != "off"
                voice = Voice(
                    kokoro_voice=spec,
                    speed=args.speed,
                    sentence_pause=pause,
                    clause_pause=float(comma) if split else Voice().clause_pause,
                    split_clauses=split,
                )
                # Like the app: one sentence at a time, each with its own pauses.
                clips = [speak(kokoro, t, voice)[0] for t in split_sentences(args.text)]
                suffix = ""
                if many:
                    c = f"{int(round(float(comma) * 1000))}ms" if split else "off"
                    suffix = f"_pause{int(round(pause * 1000))}ms_comma{c}"
                path = args.out / f"{name}{suffix}.wav"
                path.write_bytes(to_wav(np.concatenate(clips), 24000))
                print(f"{spec:<30} pause {pause:.2f} s, comma {comma} -> {path}")


if __name__ == "__main__":
    main()
