# Offline Wikipedia

An English Wikipedia she can search without internet. It runs on CPU and disk
only (no VRAM), costs nothing, and the data stays on your PC. This page covers
getting the data and building the search index. Hooking it into her answers is a
separate step.

## What you need

- About 100 GB free on a drive: the ZIM download (roughly 50 GB) plus the index
  (roughly 20 to 30 GB). The download can be deleted after the index is built.
- Keep it out of the repo and out of the Google Drive folder. `C:\data\wikipedia`
  works.

## 1. Download

Use the Kiwix copy of English Wikipedia, which is the whole encyclopedia and is
updated every few months.

1. Open [library.kiwix.org](https://library.kiwix.org) and search "Wikipedia".
2. Pick the **English** one that is **"no pictures"** (the file name contains
   `nopic`). The full text is there, including the infobox facts like birth dates.
   Avoid the picture versions, which are over 100 GB.
3. Download the `.zim` file with something that can resume (a browser download
   manager or the torrent link) to `C:\data\wikipedia\`.

The Hugging Face `wikimedia/wikipedia` dump also works as a source (a folder of
parquet files), but it turned out to be missing many of the biggest articles
(Nicolas Cage, Roman Empire), so it is not recommended.

## 2. Point the config at it

In `config.yaml` (copy the `wiki:` block from `config.example.yaml`):

```yaml
wiki:
  engine: none   # switch to "local" when the lookup is wired in
  source: C:/data/wikipedia/wikipedia_en_all_nopic_2026-01.zim   # your file's name
  index_file: C:/data/wikipedia/index.sqlite
  workers: 4       # CPU processes used while building
  max_chars_per_article: 24000
  timeout_seconds: 15
  notice_after_seconds: 1.5
  mention_source: true
```

`max_chars_per_article` keeps the opening, the infobox facts and the first
sections of each article. 24,000 reaches facts deep in long articles (4,000
missed many, and 12,000 still cut some). Most articles are shorter than this,
so the index grows by much less than double. Smaller means a smaller, faster
index.

A lookup that runs past `notice_after_seconds` gets a short "One moment, I'm
looking into it." or a similar line first (persona `lines.looking_up`, a list she
picks from at random, never the same one twice in a row). One that runs past
`timeout_seconds` is dropped and she answers without it.

With `mention_source: true` she says "Wikipedia says..." once with a looked-up
fact, which helps when checking what the lookup found. Set it to `false` and she
says the fact as if she knew it. Either way she doesn't suggest `/claude` for
general-knowledge questions; that's for things about your day and the brief.

## 3. Build the index

```powershell
cd C:\dev\desktop-assistant
uv sync --extra tts --extra stt --extra stt-gpu --extra wiki
uv run python scripts/build_wiki_index.py --limit 20000   # trial run
uv run python scripts/build_wiki_index.py --ask "What is Nicolas Cage's birthday?"
```

`uv sync` uninstalls every extra you don't list, so keep the ones you already use
(`tts` for her voice, `stt` and `stt-gpu` for Whisper) in the same command.

The trial indexes only the first 20,000 articles, so a question can come back
empty or about a different page. When it looks right, build the real one. Leave
the PC awake; it uses `workers` CPU cores, takes a few hours, and can be stopped
with Ctrl+C because the old index is only replaced when the build finishes:

```powershell
uv run python scripts/build_wiki_index.py
```

`--ask` prints the passages she would get and how long the search took.

## How it works

A ZIM stores each article as HTML. The loader (`src/secretary/wiki_zim.py`)
turns it back into text: opening paragraphs, then the infobox facts ("Facts:
Born: January 7, 1964; Occupation: Actor."), then the body up to the References
section. Navigation boxes, tables, images and reference marks are dropped.

Each article is cut into passages of about 600 characters, opening first, and
stored in a SQLite full-text index (BM25 ranking, word stems, so "directed" finds
"directs"). A search tries these in order: articles whose exact title is in the
question (the longest title wins, so "Nicolas Cage" beats "Cage"), then passages
containing all the key words, then any of them. Title matches count ten times a
body match. Very common words are dropped from the query so a question like
"history of Zanzibar" doesn't have to score every history page. Stubs and
disambiguation pages are not indexed.

Searching uses only Python's standard library. Only building needs `libzim`,
`lxml` and (for the parquet source) `pyarrow`. The code is in
`src/secretary/wiki.py` and knows nothing about the chat model, so another front
end can use it unchanged.

## 4. Turn it on

Once `--ask` gives good answers, set `engine: local` under `wiki:` in
`config.yaml` and restart the app. The startup log says "Wikipedia lookup ready".

She looks something up when the question asks for general knowledge ("who is ...",
"what is ...", "why did ...", "how tall is ...", "tell me about ..."), and always
when you say "look up ..." or "what does Wikipedia say about ...". She does not
look up questions that sound like they're about you or your day (my, brief,
calendar, email, interview, today, tomorrow), small talk, or live information
(weather, news, the time). Those go to the model as before.

The best passages (up to three, about 600 characters each) go to the model for that
one question and are not kept in the chat history. She answers in a sentence or two
and says it comes from Wikipedia. If the passages don't answer it, she says she
couldn't find it. Matching is done in code, not by the model, so it's predictable;
the log line "Wikipedia: 2 of 6 passages kept in 25 ms (...)" shows what she found.

## Checking the search

`evals/wiki_questions.yaml` holds about 30 questions, each with the article(s)
that answer it and a pattern for the answer. To run them:

```powershell
uv run python scripts/eval_wiki.py        # one line per question, then totals
uv run python scripts/eval_wiki.py -v     # also shows what she read on a miss
```

It runs the same steps as the app (decide whether to look it up, search, keep
the best passages, clip them) without the chat model, and reports:

- **article:** the right article is among the passages she gets (recall@4).
- **answer:** the answer itself is in the text she reads.
- **MRR:** how near the top the right article sits.

A miss here is a search problem. A hit that she still answers wrong is a prompt
or model problem. Run it before and after a search change to see whether the
change helped.

## Optional: a reranker

Keyword search finds passages that share the question's words. It can't tell
that "cease operations" and "was dissolved in 1799" mean the same thing, and a
common word ("adult" in "bones in the adult human body") can pull in the wrong
article. A reranker fixes much of that. It's a small model (a cross-encoder)
that reads the question and each candidate passage together and scores how well
the passage answers it. The search then gathers a wider pool: 20 passages, plus
the rest of the articles the question names. The reranker picks the best from
that pool. It runs on the CPU, so it costs no VRAM.

```powershell
uv sync --extra tts --extra stt --extra stt-gpu --extra wiki --extra rerank
uvx --from huggingface_hub hf download BAAI/bge-reranker-base onnx/model.onnx tokenizer.json --local-dir C:/data/wikipedia/bge-reranker
```

Then add this under `wiki:` in `config.yaml`:

```yaml
  rerank_model: C:/data/wikipedia/bge-reranker
```

Models tried on `evals/wiki_questions.yaml` (32 questions, `keep` mode). "12k" and
"24k" are the index's `max_chars_per_article` (12,000 or 24,000); rows without
one used the 12k index:

| model | size | answer in her text | search time (median) |
|---|---|---|---|
| none (keywords only) | | 25 | ~0.15-0.4 s |
| `cross-encoder/ms-marco-MiniLM-L6-v2` | 90 MB | 28 of 31 | ~0.6 s |
| `BAAI/bge-reranker-base` | 1.1 GB | 30 of 32 | ~3 s |
| none, 24k index | | 26 of 32 | ~0.25 s |
| `BAAI/bge-reranker-base`, 24k index | 1.1 GB | 31 of 32 | ~3-5 s |

`rerank_model` can also point at a single `.onnx` file, such as a quantized
`onnx/model_quantized.onnx`, which is smaller and faster but usually a little
less accurate. Try it with `scripts/eval_wiki.py --compare --model <file>`.

`rerank_mode` sets how its scores are used:

- `keep` (the default) takes the keyword search's top two passages, usually
  the named article's opening and its best match, then fills the rest in the
  model's order. It scored best in testing, with both models.
- `fuse` blends the keyword order with the model's order (reciprocal rank
  fusion). It tied `keep` with bge and trailed it with MiniLM.
- `rerank` uses the model's order alone. It dropped clear keyword hits such as
  "the mount of Ganesha".

`uv run python scripts/eval_wiki.py` uses the configured mode, and `--no-rerank`
runs the same questions without the reranker. `--compare` runs every question
with keyword ranking and with each mode side by side, scoring each question
once. `--ask` and the app use it as
soon as it's set. The startup log says "Wikipedia reranker ready". If the model
or the extra is missing, she carries on with keyword ranking and logs why.

Any cross-encoder exported to ONNX works the same way (one score per question
and passage pair).
