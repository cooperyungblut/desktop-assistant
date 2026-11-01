"""Save a short sample of each voice (or blend) as a WAV file so you can compare them.

    uv run python scripts/try_voices.py af_heart af_bella "af_heart:70,af_bella:30"
    uv run python scripts/try_voices.py "af_heart:50,bf_emma:50" --text "Hi Cooper!"
    uv run python scripts/try_voices.py "af_heart:80,af_nicole:20" --pause 0.25 0.35 0.5

Like the app, the text is spoken one sentence at a time with --pause seconds of
silence after each sentence (voice.sentence_pause in the persona file). Give
several pauses to get one file per pause and compare them.

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
from secretary.tts import pad_to_pause, pause_after, resolve_voice, to_wav

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
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "voice-samples")
    args = parser.parse_args()

    from kokoro_onnx import Kokoro

    cfg = TtsConfig()
    kokoro = Kokoro(str(ROOT / cfg.model_path), str(ROOT / cfg.voices_path))
    args.out.mkdir(parents=True, exist_ok=True)
    sentences = split_sentences(args.text)
    for spec in args.voices:
        style = resolve_voice(kokoro, spec)
        clips = []
        for sentence in sentences:  # synthesize once, then try each pause
            samples, rate = kokoro.create(sentence, voice=style, speed=args.speed, lang="en-us")
            clips.append((sentence, samples))
        name = re.sub(r"[^A-Za-z0-9]+", "_", spec).strip("_")
        for pause in args.pause:
            voice = Voice(sentence_pause=pause, clause_pause=min(pause, Voice().clause_pause))
            joined = np.concatenate(
                [pad_to_pause(s, rate, pause_after(t, voice)) for t, s in clips]
            )
            suffix = f"_pause{int(round(pause * 1000))}ms" if len(args.pause) > 1 else ""
            path = args.out / f"{name}{suffix}.wav"
            path.write_bytes(to_wav(joined, rate))
            print(f"{spec:<30} pause {pause:.2f} s -> {path}")


if __name__ == "__main__":
    main()
