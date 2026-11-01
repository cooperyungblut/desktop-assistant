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
