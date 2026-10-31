"""Download the Kokoro ONNX model files (about 340 MB) into ./models."""

from __future__ import annotations

import urllib.request
from pathlib import Path

BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
FILES = ["kokoro-v1.0.onnx", "voices-v1.0.bin"]
TARGET = Path(__file__).resolve().parents[1] / "models"


def main() -> None:
    TARGET.mkdir(exist_ok=True)
    for name in FILES:
        dest = TARGET / name
        if dest.is_file():
            print(f"{name}: already downloaded")
            continue
        print(f"Downloading {name} ...")
        tmp = dest.with_suffix(".part")
        urllib.request.urlretrieve(BASE + name, tmp)
        tmp.replace(dest)
        print(f"  saved {dest} ({dest.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
