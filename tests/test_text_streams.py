from secretary.llm import SentenceSplitter, ThinkFilter, split_sentences


def test_splitter_emits_whole_sentences_from_tokens():
    s = SentenceSplitter(min_chars=5)
    out = []
    for tok in ["Okay, good", " news! You have", " an interview.", " It's on Tue", "sday."]:
        out += s.feed(tok)
    out += s.flush()
    assert out == ["Okay, good news!", "You have an interview.", "It's on Tuesday."]


def test_short_fragments_are_merged():
    assert split_sentences("Hi! Okay. So the call is at ten.") == [
        "Hi! Okay. So the call is at ten."
    ]


def test_think_filter_across_chunks():
    f = ThinkFilter()
    chunks = ["<thi", "nk>planning...", "</th", "ink>Hello", " there <", "b>"]
    assert "".join(f.feed(c) for c in chunks) == "Hello there <b>"
