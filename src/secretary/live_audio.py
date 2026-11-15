"""Audio for live notes: capture, chunking at pauses, and a short memory buffer.

Live notes listen to two sources at once: your microphone ("You") and what
the computer plays ("Computer", WASAPI loopback on Windows, so the other side
of a call or a video). Each source is cut into chunks at natural pauses, so
Whisper gets whole phrases instead of words split in half, and each chunk is
transcribed on its own.

Nothing here is written to disk. `RecentAudio` keeps the last few seconds in
memory only; it is the piece a later "write that down" (with nothing after
it) can use to save what was just said.

`SoundcardSource` is the only part that touches real hardware. It needs the
`live` extra (`uv sync --extra live`); everything else is plain numpy and is
tested with synthetic audio.
"""

from __future__ import annotations

import collections
import logging
import threading
import time
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger(__name__)
SAMPLE_RATE = 16000  # what Whisper expects
FRAME = 480  # 30 ms at 16 kHz, the unit the chunker decides on

# (samples, wall-clock time at the end of the block)
BlockHandler = Callable[[np.ndarray, float], None]


@dataclass
class Chunk:
    """One stretch of speech from one source."""

    source: str  # "mic" or "computer"
    start: float  # wall-clock seconds (time.time())
    samples: np.ndarray = field(repr=False)

    @property
    def end(self) -> float:
        return self.start + self.samples.size / SAMPLE_RATE

    @property
    def seconds(self) -> float:
        return self.samples.size / SAMPLE_RATE


class Segmenter:
    """Cuts one source's audio into chunks at pauses.

    A chunk starts at the first loud frame (with a little audio before it, so
    the first consonant isn't clipped) and ends after `pause_seconds` of quiet,
    or at the quietest moment near `max_seconds` if nobody pauses. "Loud" is
    relative: the threshold follows the source's own background level, so a
    noisy room and a silent loopback both work. Whisper's own voice filter
    then removes whatever breathing or noise is left inside a chunk.
    """

    def __init__(
        self,
        source: str,
        pause_seconds: float = 0.7,
        max_seconds: float = 12.0,
        min_speech_seconds: float = 0.3,
        min_rms: float = 0.006,
        preroll_seconds: float = 0.3,
    ):
        self.source = source
        self._pause = int(pause_seconds * SAMPLE_RATE / FRAME)
        self._max = int(max_seconds * SAMPLE_RATE / FRAME)
        self._min_speech = max(1, int(min_speech_seconds * SAMPLE_RATE / FRAME))
        self._min_rms = min_rms
        self._preroll = max(1, int(preroll_seconds * SAMPLE_RATE / FRAME))
        self._floor = min_rms / 3  # background level estimate
        self._pending = np.zeros(0, dtype=np.float32)  # less than one frame
        self._pending_end = 0.0
        self._before: collections.deque[np.ndarray] = collections.deque(maxlen=self._preroll)
        self._frames: list[np.ndarray] = []  # the open chunk
        self._levels: list[float] = []
        self._start = 0.0
        self._voiced = 0
        self._quiet = 0  # quiet frames at the end of the open chunk
        self._last_end: float | None = None  # wall time the last block ended

    @property
    def threshold(self) -> float:
        return max(self._min_rms, self._floor * 3)

    @property
    def open(self) -> bool:
        return bool(self._frames)

    @property
    def open_since(self) -> float | None:
        return self._start if self._frames else None

    def feed(self, samples: np.ndarray, end_time: float) -> list[Chunk]:
        """Add a block of 16 kHz mono float32 samples that ended at `end_time`."""
        out: list[Chunk] = []
        samples = np.asarray(samples, dtype=np.float32).reshape(-1)
        start_time = end_time - samples.size / SAMPLE_RATE
        # WASAPI loopback sends nothing at all while nothing plays, so a jump in
        # time means silence that never arrived as samples: end the chunk there.
        if self._last_end is not None and start_time - self._last_end > self._pause_time:
            out += self._close()
            self._pending = np.zeros(0, dtype=np.float32)
            self._before.clear()
        self._last_end = end_time
        data = np.concatenate([self._pending, samples]) if self._pending.size else samples
        whole = data.size // FRAME * FRAME
        # Wall time of the first sample in `data`.
        t = end_time - data.size / SAMPLE_RATE
        for i in range(0, whole, FRAME):
            out += self._frame(data[i : i + FRAME], t + i / SAMPLE_RATE)
        self._pending = data[whole:].copy()
        return out

    @property
    def _pause_time(self) -> float:
        return self._pause * FRAME / SAMPLE_RATE

    def idle(self, now: float) -> list[Chunk]:
        """Close the open chunk if no audio has come in for a pause's length."""
        if self._frames and self._last_end is not None and now - self._last_end > self._pause_time:
            return self._close()
        return []

    def flush(self) -> list[Chunk]:
        """Close whatever is open (when listening stops)."""
        return self._close()

    def _frame(self, frame: np.ndarray, t: float) -> list[Chunk]:
        rms = float(np.sqrt(np.mean(frame * frame)))
        loud = rms >= self.threshold
        # Follow the background level: drop to quieter moments at once, creep up
        # slowly (doubling in ~4 s), so steady noise like a fan stops counting
        # as speech but the pauses between words keep the level down.
        self._floor = min(max(rms, 1e-5), self._floor * 1.005)
        if not self._frames:
            if loud:
                self._start = t - len(self._before) * FRAME / SAMPLE_RATE
                self._frames = [*self._before, frame]
                self._levels = [0.0] * len(self._before) + [rms]
                self._before.clear()
                self._voiced, self._quiet = 1, 0
            else:
                self._before.append(frame)
            return []
        self._frames.append(frame)
        self._levels.append(rms)
        if loud:
            self._voiced += 1
            self._quiet = 0
        else:
            self._quiet += 1
        if self._quiet >= self._pause:
            return self._close()
        if len(self._frames) >= self._max:
            return self._cut()
        return []

    def _cut(self) -> list[Chunk]:
        """Nobody paused: cut at the quietest frame of the last few seconds and
        carry the rest over into the next chunk."""
        window = min(len(self._levels) // 3, int(3 * SAMPLE_RATE / FRAME))
        tail = self._levels[-window:]
        at = len(self._levels) - window + int(np.argmin(tail)) + 1
        rest_frames, rest_levels = self._frames[at:], self._levels[at:]
        self._frames, self._levels = self._frames[:at], self._levels[:at]
        rest_start = self._start + at * FRAME / SAMPLE_RATE
        self._quiet = 0
        out = self._close()
        if rest_frames:
            self._frames, self._levels = rest_frames, rest_levels
            self._start = rest_start
            self._voiced = sum(1 for level in rest_levels if level >= self.threshold)
            self._quiet = 0
        return out

    def _close(self) -> list[Chunk]:
        frames, voiced, quiet = self._frames, self._voiced, self._quiet
        self._frames, self._levels, self._voiced, self._quiet = [], [], 0, 0
        if not frames or voiced < self._min_speech:
            return []
        keep = len(frames) - max(0, quiet - self._preroll)  # a little quiet at the end
        return [Chunk(self.source, self._start, np.concatenate(frames[:keep]))]


class RecentAudio:
    """The last few seconds of a source, in memory only."""

    def __init__(self, seconds: float = 15.0):
        self._limit = int(seconds * SAMPLE_RATE)
        self._blocks: collections.deque[tuple[float, np.ndarray]] = collections.deque()
        self._size = 0
        self._lock = threading.Lock()

    def add(self, samples: np.ndarray, end_time: float) -> None:
        with self._lock:
            self._blocks.append((end_time, samples))
            self._size += samples.size
            while self._blocks and self._size - self._blocks[0][1].size >= self._limit:
                self._size -= self._blocks.popleft()[1].size

    def last(self, seconds: float | None = None) -> np.ndarray:
        """Up to the last `seconds` of audio (all that's kept by default)."""
        with self._lock:
            blocks = [b for _, b in self._blocks]
        if not blocks:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(blocks)
        want = self._limit if seconds is None else int(seconds * SAMPLE_RATE)
        return audio[-want:]

    def clear(self) -> None:
        with self._lock:
            self._blocks.clear()
            self._size = 0


class SoundcardSource:
    """A microphone, or the computer's own output (loopback), via `soundcard`.

    Runs a reader thread that hands 16 kHz mono blocks to `on_block`. On
    Windows, WASAPI converts from the device's own rate in shared mode.
    """

    def __init__(self, source: str, device: str | None = None, block_seconds: float = 0.1):
        self.source = source  # "mic" or "computer"
        self._device = device or None
        self._block = int(block_seconds * SAMPLE_RATE)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.error: str | None = None
        self.name: str | None = None

    def _open(self):
        import soundcard as sc

        if self.source == "computer":
            speaker = sc.get_speaker(self._device) if self._device else sc.default_speaker()
            # A speaker's loopback is listed as a microphone with the speaker's name.
            return sc.get_microphone(str(speaker.name), include_loopback=True)
        return sc.get_microphone(self._device) if self._device else sc.default_microphone()

    def start(self, on_block: BlockHandler) -> None:
        mic = self._open()  # raises on a missing device, before the thread starts
        self.name = str(mic.name)
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(mic, on_block), name=f"live-{self.source}", daemon=True
        )
        self._thread.start()

    def _run(self, mic, on_block: BlockHandler) -> None:
        try:
            with warnings.catch_warnings():
                # "data discontinuity in recording": a dropped block; harmless here.
                warnings.simplefilter("ignore")
                with mic.recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=self._block) as r:
                    while not self._stop.is_set():
                        data = r.record(numframes=self._block)
                        if data.size:
                            on_block(_mono(data), time.time())
        except Exception as exc:  # unplugged device, driver error, ...
            self.error = str(exc)
            log.warning("Live notes stopped hearing %s: %s", self.source, exc)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None


def _mono(data: np.ndarray) -> np.ndarray:
    data = np.asarray(data, dtype=np.float32)
    return data.mean(axis=1) if data.ndim == 2 else data.reshape(-1)


def list_devices() -> dict[str, list[str]]:
    """Names of the microphones and speakers `soundcard` can see."""
    import soundcard as sc

    return {
        "microphones": [str(m.name) for m in sc.all_microphones()],
        "speakers": [str(s.name) for s in sc.all_speakers()],
        "default_microphone": [str(sc.default_microphone().name)],
        "default_speaker": [str(sc.default_speaker().name)],
    }
