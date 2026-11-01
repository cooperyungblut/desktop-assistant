import sys
import types

import pytest

from secretary.config import SttConfig
from secretary.stt import (
    NoTranscriber,
    SttUnavailable,
    WhisperTranscriber,
    clean_transcript,
    make_transcriber,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("  When is my   coffee chat? ", "When is my coffee chat?"),
        ("Thank you.", "Thank you."),  # a real thing to say to an assistant
        ("You", ""),  # classic Whisper output for near-silence
        (" Thanks for watching! ", ""),
        ("...", ""),
        ("", ""),
    ],
)
def test_clean_transcript(raw, expected):
    assert clean_transcript(raw) == expected


def test_engine_none_gives_a_clear_error():
    t = make_transcriber(SttConfig(engine="none"))
    assert isinstance(t, NoTranscriber) and not t.available


async def test_no_transcriber_raises():
    with pytest.raises(SttUnavailable, match="turned off"):
        await NoTranscriber().transcribe(b"x")


class _Seg:
    def __init__(self, text):
        self.text = text


class _FakeWhisperModel:
    """Stands in for faster_whisper.WhisperModel; records how it was built and called."""

    instances: list["_FakeWhisperModel"] = []
    fail_on: set[str] = set()

    def __init__(self, name, device, compute_type, download_root):
        if device in self.fail_on:
            raise RuntimeError(f"cublas64_12.dll not found ({device})")
        self.name, self.device, self.compute_type = name, device, compute_type
        self.calls = []
        _FakeWhisperModel.instances.append(self)

    def transcribe(self, audio, **kwargs):
        self.calls.append(kwargs)
        return iter([_Seg(" When is my"), _Seg(" coffee chat?")]), None


@pytest.fixture
def fake_faster_whisper(monkeypatch):
    _FakeWhisperModel.instances = []
    _FakeWhisperModel.fail_on = set()
    module = types.SimpleNamespace(WhisperModel=_FakeWhisperModel)
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    return _FakeWhisperModel


async def test_whisper_loads_and_transcribes(fake_faster_whisper, tmp_path):
    pytest.importorskip("av")
    t = WhisperTranscriber(SttConfig(download_dir=tmp_path))
    await t.load()
    assert t.available
    assert await t.transcribe(_encode_tone(1.0), "Names: Dana Reyes.") == "When is my coffee chat?"
    model = fake_faster_whisper.instances[-1]
    assert (model.device, model.compute_type) == ("cpu", "int8")
    assert model.calls[-1]["vad_filter"] is True
    assert model.calls[-1]["initial_prompt"] == "Names: Dana Reyes."


async def test_cuda_failure_falls_back_to_cpu(fake_faster_whisper, tmp_path):
    fake_faster_whisper.fail_on = {"cuda"}
    t = WhisperTranscriber(
        SttConfig(device="cuda", compute_type="int8_float16", download_dir=tmp_path)
    )
    await t.load()
    assert t.available
    model = fake_faster_whisper.instances[-1]
    assert (model.device, model.compute_type) == ("cpu", "int8")


async def test_load_failure_disables_speech_input(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)  # import fails
    t = WhisperTranscriber(SttConfig(download_dir=tmp_path))
    await t.load()
    assert not t.available
    with pytest.raises(SttUnavailable):
        await t.transcribe(b"x")


def _encode_tone(seconds: float, container: str = "webm", codec: str = "libopus") -> bytes:
    """Make the kind of clip Chromium's MediaRecorder sends: Opus in WebM, 48 kHz."""
    import io

    import av
    import numpy as np

    rate = 48000
    t = np.arange(int(rate * seconds)) / rate
    pcm = (0.3 * np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with av.open(buf, mode="w", format=container) as out:
        stream = out.add_stream(codec, rate=rate, layout="mono")
        for start in range(0, len(pcm), 960):
            frame = av.AudioFrame.from_ndarray(
                pcm[start : start + 960].reshape(1, -1), format="s16", layout="mono"
            )
            frame.rate = rate
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    return buf.getvalue()


def test_decode_browser_style_webm_to_16k_mono():
    pytest.importorskip("av")
    from secretary.stt import SAMPLE_RATE, decode_audio

    samples = decode_audio(_encode_tone(1.5))
    assert samples.dtype.name == "float32"
    assert abs(len(samples) / SAMPLE_RATE - 1.5) < 0.1
    assert 0.2 < float(abs(samples).max()) <= 1.0


def test_decode_garbage_raises_a_clear_error():
    pytest.importorskip("av")
    from secretary.stt import decode_audio

    with pytest.raises(SttUnavailable, match="decode"):
        decode_audio(b"definitely not audio")
