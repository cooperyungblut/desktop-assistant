"""Re-rank Wikipedia passages with a small cross-encoder on the CPU.

Keyword search (wiki.py) finds candidates fast but only by shared words: it
can't tell that "cease operations" and "was dissolved in 1799" mean the same
thing, and a common word ("adult") can pull in the wrong article. A
cross-encoder reads the question and one passage together and scores how well
the passage answers it, so it can pick the right passage from a wider pool.

`RerankedWiki` wraps the index and keeps its `search()` signature, so the app,
the eval and `--ask` use it unchanged:

1. ask the index for more candidates than needed (CANDIDATES),
2. add the other passages of the articles the question names (the VOC's
   dissolution sits deep in its article, worded nothing like the question),
3. score every candidate with the model,
4. order them by `mode`:
   - "rerank": by the model's score alone,
   - "fuse":   by reciprocal rank fusion of the keyword order and the model's
               order (1/(K+keyword rank) + 1/(K+model rank)), so a clear
               keyword hit (the article the question names) isn't thrown out
               because a small model misreads one word ("mount" as a mountain),
   - "keep":   the keyword search's first KEPT passages, then the model's order
               (the default: best on evals/wiki_questions.yaml, 28/31 answers
               vs 25 keyword-only, 26 fuse, 27 rerank).

The model is any cross-encoder exported to ONNX with a `tokenizer.json` beside
it (docs/wikipedia.md: ms-marco-MiniLM-L6-v2, about 90 MB). Needs the `rerank`
extra (onnxruntime, tokenizers); no VRAM.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from .wiki import FACTS_PREFIX, Passage, WikiIndex

log = logging.getLogger(__name__)

CANDIDATES = 20  # passages asked of the keyword search
EXPANDED_ARTICLES = 2  # named articles whose other passages join the pool
MAX_POOL = 100  # passages scored per question (time grows with this)
MAX_TOKENS = 256  # question + passage, cut beyond this
BATCH = 16
FUSION_K = 10  # small: with ~100 candidates, the top few ranks should count most
KEPT = 2  # passages "keep" mode takes from the keyword order first
MODES = ("fuse", "rerank", "keep")


class Scorer(Protocol):
    def score(self, question: str, texts: Sequence[str]) -> list[float]: ...


class CrossEncoder:
    """An ONNX cross-encoder: higher score = better answer to the question."""

    def __init__(self, model_dir: Path, max_tokens: int = MAX_TOKENS):
        import onnxruntime
        from tokenizers import Tokenizer

        model_dir = Path(model_dir)
        model = next(
            (
                p
                for p in (model_dir / "model.onnx", model_dir / "onnx" / "model.onnx")
                if p.exists()
            ),
            None,
        )
        tokenizer = model_dir / "tokenizer.json"
        if model is None or not tokenizer.exists():
            raise FileNotFoundError(
                f"{model_dir} needs model.onnx (or onnx/model.onnx) and tokenizer.json"
            )
        self._tokenizer = Tokenizer.from_file(str(tokenizer))
        self._tokenizer.enable_truncation(max_length=max_tokens)
        self._tokenizer.enable_padding()
        options = onnxruntime.SessionOptions()
        options.log_severity_level = 3
        self._session = onnxruntime.InferenceSession(
            str(model), options, providers=["CPUExecutionProvider"]
        )
        self._inputs = {i.name for i in self._session.get_inputs()}

    def score(self, question: str, texts: Sequence[str]) -> list[float]:
        import numpy as np

        scores: list[float] = []
        for start in range(0, len(texts), BATCH):
            batch = self._tokenizer.encode_batch(
                [(question, t) for t in texts[start : start + BATCH]]
            )
            feed = {
                "input_ids": np.array([e.ids for e in batch], dtype=np.int64),
                "attention_mask": np.array([e.attention_mask for e in batch], dtype=np.int64),
                "token_type_ids": np.array([e.type_ids for e in batch], dtype=np.int64),
            }
            (logits,) = self._session.run(
                None, {k: v for k, v in feed.items() if k in self._inputs}
            )
            scores.extend(float(row[0]) for row in np.asarray(logits).reshape(len(batch), -1))
        return scores


class RerankedWiki:
    """The index's search, with the results re-ordered by a cross-encoder."""

    def __init__(self, index: WikiIndex, scorer: Scorer, mode: str = "keep"):
        if mode not in MODES:
            raise ValueError(f"unknown rerank mode {mode!r}; use one of {', '.join(MODES)}")
        self.index = index
        self.scorer = scorer
        self.mode = mode
        self.trace: list[tuple[str, float]] = []
        # The last question's pool and scores, so the eval can compare modes
        # without scoring the same passages again.
        self._last: tuple[tuple[str, int, bool], list[Passage], list[float], list] | None = None

    def search(self, question: str, limit: int = 3, *, loose: bool = True) -> list[Passage]:
        key = (question, limit, loose)
        if self._last is not None and self._last[0] == key:
            _, pool, scores, trace = self._last
        else:
            started = time.perf_counter()
            found = self.index.search(question, max(limit, CANDIDATES), loose=loose)
            trace = [*getattr(self.index, "trace", [])]
            pool = _with_article_passages(self.index, found)[:MAX_POOL]
            scores = self.scorer.score(question, [f"{p.title}. {p.text}" for p in pool])
            trace.append(("rerank", (time.perf_counter() - started) * 1000))
            self._last = (key, pool, scores, trace)
        self.trace = trace
        return order(pool, scores, self.mode)[:limit]

    def interrupt(self) -> None:
        self.index.interrupt()

    def warm_up(self) -> float:
        return self.index.warm_up()

    def close(self) -> None:
        self.index.close()

    def __enter__(self) -> RerankedWiki:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def order(pool: list[Passage], scores: list[float], mode: str) -> list[Passage]:
    """`pool` (in keyword order) re-ordered by `mode` (see the module notes)."""
    by_model = sorted(range(len(pool)), key=lambda i: -scores[i])  # stable on ties
    if mode == "rerank":
        picked = [(scores[i], i) for i in by_model]
    elif mode == "keep":
        kept = list(range(min(KEPT, len(pool))))
        rest = [i for i in by_model if i not in kept]
        picked = [(scores[i], i) for i in kept + rest]
    else:
        model_rank = {i: at for at, i in enumerate(by_model)}
        fused = [
            (1 / (FUSION_K + 1 + i) + 1 / (FUSION_K + 1 + model_rank[i]), i)
            for i in range(len(pool))
        ]
        picked = sorted(fused, key=lambda pair: (-pair[0], pair[1]))
    return [
        Passage(pool[i].title, pool[i].text, score=score, match=pool[i].match)
        for score, i in picked
    ]


def _with_article_passages(index: WikiIndex, found: list[Passage]) -> list[Passage]:
    """The search's passages, then the rest of the articles the question names."""
    pool = list(found)
    seen = {(p.title, p.text) for p in pool}
    named = list(dict.fromkeys(p.title for p in found if p.match == "title"))
    for title in named[:EXPANDED_ARTICLES]:
        for text in index.article_texts(title):
            # The Facts line is already in the pool, with the asked-about rows first.
            if (title, text) in seen or FACTS_PREFIX in text:
                continue
            seen.add((title, text))
            pool.append(Passage(title, text, score=0.0, match="title"))
    return pool


def make_reranked(
    index: WikiIndex, model_dir: Path | None, mode: str = "keep"
) -> WikiIndex | RerankedWiki:
    """`index` wrapped with the reranker in `model_dir`, or `index` as is when
    there is no model or it can't load (she still answers, just without it)."""
    if model_dir is None:
        return index
    try:
        scorer = CrossEncoder(model_dir)
    except ImportError:
        log.warning("wiki.rerank_model is set but the rerank extra isn't installed")
        return index
    except Exception as exc:  # a missing or broken model file
        log.warning("Wikipedia reranker not loaded: %s", exc)
        return index
    log.info("Wikipedia reranker ready (%s)", model_dir)
    return RerankedWiki(index, scorer, mode)
