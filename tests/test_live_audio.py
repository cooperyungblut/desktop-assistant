import numpy as np

from secretary.live_audio import SAMPLE_RATE, RecentAudio, Segmenter

RNG = np.random.default_rng(0)


def tone(seconds, level=0.2):
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    return (level * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def quiet(seconds, level=0.001):
    return (level * RNG.standard_normal(int(seconds * SAMPLE_RATE))).astype(np.float32)


def feed_all(seg, audio, start=1000.0, block=0.1):
    """Feed `audio` in blocks as a capture thread would; return the chunks."""
    out, n, size = [], 0, int(block * SAMPLE_RATE)
    while n < audio.size:
        part = audio[n : n + size]
        n += part.size
        out += seg.feed(part, start + n / SAMPLE_RATE)
    return out


def test_two_phrases_with_a_pause_are_two_chunks():
    seg = Segmenter("mic", pause_seconds=0.7)
    audio = np.concatenate([quiet(1), tone(1.5), quiet(1.2), tone(2), quiet(1)])
    chunks = feed_all(seg, audio)
    assert len(chunks) == 2
    first, second = chunks
    assert abs(first.start - 1001.0) < 0.35  # starts a little before the sound
    assert 1.5 <= first.seconds <= 2.3
    assert abs(second.start - 1003.7) < 0.35
    assert all(c.source == "mic" for c in chunks)


def test_a_short_breath_between_words_stays_in_one_chunk():
    seg = Segmenter("mic", pause_seconds=0.7)
    audio = np.concatenate([tone(1), quiet(0.3), tone(1), quiet(1)])
    chunks = feed_all(seg, audio)
    assert len(chunks) == 1
    assert chunks[0].seconds >= 2.2


def test_a_click_is_not_speech():
    seg = Segmenter("mic", min_speech_seconds=0.3)
    audio = np.concatenate([quiet(1), tone(0.06), quiet(1.5)])
    assert feed_all(seg, audio) == []


def test_nonstop_talk_is_cut_near_the_limit_at_a_quiet_moment():
    seg = Segmenter("computer", max_seconds=6)
    # 5 s of talk, a dip that's too short to count as a pause, then 5 s more.
    audio = np.concatenate([tone(5.0), tone(0.3, level=0.004), tone(5.0), quiet(1)])
    chunks = feed_all(seg, audio)
    assert len(chunks) == 2
    assert 4.9 <= chunks[0].seconds <= 5.5  # cut in the dip, not at 6 s
    assert abs(chunks[1].start - chunks[0].end) < 0.05  # nothing lost


def test_a_gap_in_loopback_data_ends_the_chunk():
    # WASAPI loopback delivers nothing while nothing plays.
    seg = Segmenter("computer")
    chunks = feed_all(seg, tone(1.5), start=0.0)
    chunks += feed_all(seg, tone(1.0), start=10.0)
    assert len(chunks) == 1 and chunks[0].seconds >= 1.4
    assert seg.open
    assert seg.idle(11.2) == []  # too soon
    late = seg.idle(13.0)
    assert len(late) == 1 and abs(late[0].start - 10.0) < 0.1


def test_threshold_follows_a_noisy_room():
    seg = Segmenter("mic", min_rms=0.006)
    feed_all(seg, quiet(15, level=0.01))  # a fan, louder than min_rms
    assert seg.threshold > 0.02
    seg.flush()
    chunks = feed_all(seg, np.concatenate([tone(1.0), quiet(1.5, level=0.01)]), start=1015.0)
    assert len(chunks) == 1 and chunks[0].seconds < 2


def test_flush_returns_the_open_chunk():
    seg = Segmenter("mic")
    assert feed_all(seg, tone(1.0)) == []
    assert len(seg.flush()) == 1
    assert seg.flush() == []


def test_recent_audio_keeps_only_the_last_seconds():
    buf = RecentAudio(seconds=2)
    for i in range(50):
        buf.add(np.full(1600, i, dtype=np.float32), float(i))
    audio = buf.last()
    assert 2 * SAMPLE_RATE <= audio.size <= 2 * SAMPLE_RATE + 1600
    assert audio[-1] == 49
    assert buf.last(0.5).size == SAMPLE_RATE // 2
    buf.clear()
    assert buf.last().size == 0
