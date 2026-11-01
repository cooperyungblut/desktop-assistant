import numpy as np
import pytest

from secretary.tts import parse_voice_spec, resolve_voice


def test_single_voice_passes_through():
    assert parse_voice_spec("af_heart") == [("af_heart", 1.0)]
    assert resolve_voice(None, "af_heart") == "af_heart"


def test_blend_weights_are_normalized():
    assert parse_voice_spec("af_heart:70, af_bella:30") == [("af_heart", 0.7), ("af_bella", 0.3)]


@pytest.mark.parametrize("spec", ["", "af_heart:x", "af_heart:0,af_bella:1"])
def test_bad_specs_are_rejected(spec):
    with pytest.raises(ValueError):
        parse_voice_spec(spec)


def test_blend_is_weighted_average_of_styles():
    class FakeKokoro:
        styles = {"a": np.zeros((2, 3)), "b": np.ones((2, 3))}

        def get_voice_style(self, name):
            return self.styles[name]

    style = resolve_voice(FakeKokoro(), "a:75,b:25")
    assert np.allclose(style, 0.25)


def test_pause_depends_on_how_the_chunk_ends():
    from secretary.persona import Voice
    from secretary.tts import pause_after

    v = Voice(sentence_pause=0.4, clause_pause=0.1)
    assert pause_after("You got it.", v) == 0.4
    assert pause_after('She said "yes!"', v) == 0.4
    assert pause_after("First, the interview,", v) == 0.1
    assert pause_after("no punctuation", v) == 0.4


def test_clip_is_padded_to_the_pause_counting_its_own_silence():
    from secretary.tts import pad_to_pause, trailing_silence

    rate = 1000
    tone = np.sin(np.arange(500) / 3).astype(np.float32)
    clip = np.concatenate([tone, np.zeros(100, dtype=np.float32)])  # 0.1 s tail already
    assert trailing_silence(clip, rate) == pytest.approx(0.1, abs=0.011)
    padded = pad_to_pause(clip, rate, 0.35)
    assert trailing_silence(padded, rate) == pytest.approx(0.35, abs=0.011)
    assert len(pad_to_pause(clip, rate, 0.05)) == len(clip)  # never trims
