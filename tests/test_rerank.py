from __future__ import annotations

from pathlib import Path

import pytest

from secretary.rerank import CrossEncoder, RerankedWiki, make_reranked, order
from secretary.wiki import Passage

VOC = "Dutch East India Company"
OPENING = Passage(VOC, "The VOC was a chartered trading company founded in 1602.", 100.0, "title")
FACTS = Passage(VOC, "Facts: Founded: 20 March 1602; Defunct: 31 December 1799.", 99.0, "title")
NOISE = Passage("Batavia (1628 ship)", "Batavia was a ship of the VOC.", 5.0, "all")
DEEP = "The company was formally dissolved on 31 December 1799 after years of debt."


class FakeIndex:
    def __init__(self, found: list[Passage], articles: dict[str, list[str]]):
        self.found = found
        self.articles = articles
        self.asked: list[tuple[str, int, bool]] = []
        self.trace = [("exact title", 1.0)]
        self.interrupted = False

    def search(self, question: str, limit: int = 3, *, loose: bool = True) -> list[Passage]:
        self.asked.append((question, limit, loose))
        return self.found[:limit]

    def article_texts(self, title: str) -> list[str]:
        return self.articles.get(title, [])

    def interrupt(self) -> None:
        self.interrupted = True

    def close(self) -> None:
        pass


class WordScorer:
    """Scores a passage by how many of the given words it holds."""

    def __init__(self, *words: str):
        self.words = words
        self.seen: list[str] = []

    def score(self, question: str, texts):
        self.seen = list(texts)
        return [float(sum(w in t for w in self.words)) for t in texts]


def make(scorer=None, mode: str = "rerank") -> tuple[RerankedWiki, FakeIndex]:
    index = FakeIndex(
        [OPENING, FACTS, NOISE],
        # The raw Facts passage and the opening are already in the pool.
        {VOC: [OPENING.text, "Facts: Founded: 20 March 1602.", DEEP, "It traded spices."]},
    )
    return RerankedWiki(index, scorer or WordScorer("dissolved", "1799"), mode), index


def test_a_deep_passage_of_the_named_article_can_win():
    wiki, index = make()
    found = wiki.search("In which year did the VOC cease operations?", 2, loose=False)
    assert [p.text for p in found] == [DEEP, FACTS.text]
    assert found[0].title == VOC and found[0].match == "title"
    # More candidates than asked for, with the caller's loose setting.
    assert index.asked == [("In which year did the VOC cease operations?", 20, False)]
    assert wiki.trace[0] == ("exact title", 1.0) and wiki.trace[-1][0] == "rerank"


def test_the_pool_has_no_repeats_and_no_raw_facts_line():
    scorer = WordScorer("x")
    wiki, _ = make(scorer)
    wiki.search("When did the VOC end?", 8)
    assert len(scorer.seen) == 5  # opening, Facts, Batavia, the deep passage, spices
    assert sum("Facts:" in t for t in scorer.seen) == 1
    assert all(t.startswith(f"{VOC}. ") or t.startswith("Batavia") for t in scorer.seen)


def test_only_named_articles_are_expanded():
    index = FakeIndex([NOISE], {"Batavia (1628 ship)": ["It sank in 1629."]})
    scorer = WordScorer("ship")
    RerankedWiki(index, scorer).search("Which ship sank?", 8)
    assert len(scorer.seen) == 1


def test_nothing_found_scores_nothing():
    index = FakeIndex([], {})
    assert RerankedWiki(index, WordScorer("x")).search("Who?", 8) == []


def test_interrupt_reaches_the_index():
    wiki, index = make()
    wiki.interrupt()
    assert index.interrupted


def test_without_a_model_the_index_is_used_as_is(tmp_path):
    index = FakeIndex([], {})
    assert make_reranked(index, None) is index
    assert make_reranked(index, tmp_path / "missing") is index


def _tiny_model(folder: Path) -> None:
    """A stand-in cross-encoder: its score is the passage's token count."""
    onnx = pytest.importorskip("onnx")
    pytest.importorskip("onnxruntime")
    tokenizers = pytest.importorskip("tokenizers")
    from onnx import TensorProto, helper

    vocab = {"[PAD]": 0, "[UNK]": 1, "[CLS]": 2, "[SEP]": 3, "when": 4, "a": 5, "b": 6}
    tok = tokenizers.Tokenizer(tokenizers.models.WordLevel(vocab, unk_token="[UNK]"))
    tok.pre_tokenizer = tokenizers.pre_tokenizers.Whitespace()
    tok.post_processor = tokenizers.processors.TemplateProcessing(
        single="[CLS] $A [SEP]",
        pair="[CLS] $A [SEP] $B:1 [SEP]:1",
        special_tokens=[("[CLS]", 2), ("[SEP]", 3)],
    )
    tok.save(str(folder / "tokenizer.json"))
    inputs = [
        helper.make_tensor_value_info(name, TensorProto.INT64, ["n", "t"])
        for name in ("input_ids", "attention_mask", "token_type_ids")
    ]
    nodes = [
        helper.make_node("Mul", ["attention_mask", "token_type_ids"], ["second"]),
        helper.make_node("Cast", ["second"], ["f"], to=TensorProto.FLOAT),
        helper.make_node("ReduceSum", ["f", "axes"], ["logits"], keepdims=1),
    ]
    axes = helper.make_tensor("axes", TensorProto.INT64, [1], [1])
    output = helper.make_tensor_value_info("logits", TensorProto.FLOAT, ["n", 1])
    graph = helper.make_graph(nodes, "tiny", inputs, [output], initializer=[axes])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    (folder / "onnx").mkdir()
    onnx.save(model, str(folder / "onnx" / "model.onnx"))


def test_cross_encoder_scores_question_passage_pairs(tmp_path):
    _tiny_model(tmp_path)
    encoder = CrossEncoder(tmp_path, max_tokens=8)
    texts = ["a", "a b a", "b"] * 7  # more than one batch, mixed lengths
    scores = encoder.score("when", texts)
    # Passage tokens + the closing [SEP]; the 8-token cap cuts nothing here.
    assert scores == [2.0, 4.0, 2.0] * 7
    long = encoder.score("when", ["a " * 20])
    assert long == [8.0 - 3.0]  # [CLS] when [SEP] + 5 passage tokens incl. [SEP]


def _pool(n: int) -> list[Passage]:
    return [Passage(f"T{i}", f"text {i}", 0.0, "all") for i in range(n)]


def test_fuse_keeps_a_clear_keyword_hit_the_model_dislikes():
    pool = _pool(10)
    # The model likes the keyword order backwards, except it also likes #8.
    scores = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 9.5, 8.0]
    assert [p.title for p in order(pool, scores, "rerank")][:2] == ["T8", "T9"]
    fused = [p.title for p in order(pool, scores, "fuse")]
    # Keyword #1 stays near the top; the model's favourite still climbs.
    assert fused.index("T0") < 4 and fused.index("T8") < 4
    assert len(fused) == 10 and len(set(fused)) == 10


def test_keep_takes_the_keyword_top_two_then_the_model():
    pool = _pool(5)
    scores = [0.0, 1.0, 3.0, 4.0, 5.0]
    assert [p.title for p in order(pool, scores, "keep")] == ["T0", "T1", "T4", "T3", "T2"]


def test_modes_reuse_the_scores_of_the_same_question():
    scorer = WordScorer("dissolved")
    calls = []
    original = scorer.score
    scorer.score = lambda q, texts: calls.append(q) or original(q, texts)
    wiki, _ = make(scorer)
    first = wiki.search("When did the VOC end?", 4)
    wiki.mode = "fuse"
    wiki.search("When did the VOC end?", 4)
    assert len(calls) == 1 and first[0].text == DEEP
    wiki.search("Something else?", 4)
    assert len(calls) == 2


def test_an_unknown_mode_is_refused():
    with pytest.raises(ValueError):
        RerankedWiki(FakeIndex([], {}), WordScorer("x"), "best")


def test_find_model_takes_a_folder_or_one_onnx_file(tmp_path):
    from secretary.rerank import find_model

    (tmp_path / "onnx").mkdir()
    (tmp_path / "tokenizer.json").write_text("{}")
    quantized = tmp_path / "onnx" / "model_quantized.onnx"
    quantized.write_bytes(b"")
    assert find_model(tmp_path) == (quantized, tmp_path / "tokenizer.json")
    full = tmp_path / "onnx" / "model.onnx"
    full.write_bytes(b"")
    assert find_model(tmp_path)[0] == full  # the full model wins in a folder
    assert find_model(quantized) == (quantized, tmp_path / "tokenizer.json")
    with pytest.raises(FileNotFoundError):
        find_model(tmp_path / "missing")
