from __future__ import annotations

from pathlib import Path

import pytest

from secretary.rerank import CrossEncoder, RerankedWiki, make_reranked
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


def make(scorer=None) -> tuple[RerankedWiki, FakeIndex]:
    index = FakeIndex(
        [OPENING, FACTS, NOISE],
        # The raw Facts passage and the opening are already in the pool.
        {VOC: [OPENING.text, "Facts: Founded: 20 March 1602.", DEEP, "It traded spices."]},
    )
    return RerankedWiki(index, scorer or WordScorer("dissolved", "1799")), index


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
