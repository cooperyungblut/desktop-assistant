# Offline Wikipedia

An English Wikipedia she can search without internet. It runs on CPU and disk
only (no VRAM), costs nothing, and the data stays on your PC. This page covers
getting the data and building the search index. Hooking it into her answers is a
separate step.

## What you need

- About 40 GB free on a drive (about 12 GB download plus the index; see the note on size).
- Do not put it inside the repo or the Google Drive folder. `C:\data\wikipedia` works.

## 1. Download

The data is the cleaned English text on Hugging Face (`wikimedia/wikipedia`),
the November 2023 snapshot, as 41 parquet files. In PowerShell:

```powershell
mkdir C:\data\wikipedia
uvx --from huggingface_hub hf download wikimedia/wikipedia --repo-type dataset --include "20231101.en/*" --local-dir C:\data\wikipedia
```

It resumes if interrupted; run the same command again.

What it leaves out: infoboxes, tables, images, the references / see also /
external links sections, and anything after the snapshot date. Facts that sit
in a table rather than the text (exact populations, box-office totals, season
stats) are the likely gaps. Biographies' birth dates are in the first
sentence, so those work.

## 2. Point the config at it

In `config.yaml` (copy the `wiki:` block from `config.example.yaml`):

```yaml
wiki:
  engine: none   # switch to "local" when the lookup is wired in
  source_dir: C:/data/wikipedia/20231101.en
  index_file: C:/data/wikipedia/index.sqlite
  max_chars_per_article: 4000
```

`max_chars_per_article` keeps the opening and the first sections of each
article. Smaller means a smaller, faster index; larger gives deeper answers.

## 3. Build the index

```powershell
cd C:\dev\desktop-assistant
uv sync --extra wiki
uv run python scripts/build_wiki_index.py --limit 20000   # trial run, a minute or two
uv run python scripts/build_wiki_index.py --ask "What is Nicolas Cage's birthday?"
```

The trial index has only the first 20,000 articles, so a question can come
back empty or about a different page. When you are happy with how it runs, build
the real one (leave the PC awake; about an hour or two, and you can stop it with
Ctrl+C at any time because the old index is only replaced when the build finishes):

```powershell
uv run python scripts/build_wiki_index.py
```

Then ask it things. `--ask` prints the passages she would get and how long the
search took.

If you built a trial index before this fix, delete it and build again; the layout
changed.

## How it works

Each article is cut into passages of about 600 characters, opening first, and
stored in a SQLite full-text index (BM25 ranking, word stems, so "directed"
finds "directs"). A search tries these in order: an article whose exact title is
in the question, then passages containing all the key words, then any of them.
Title matches count ten times a body match. Very common words are dropped from
the query so a question like "history of Zanzibar" doesn't have to score every
history page. Stubs and disambiguation pages are not indexed.

Searching uses only Python's standard library. Only building needs `pyarrow`.
The code is in `src/secretary/wiki.py` and knows nothing about the chat model,
so another front end can use it unchanged.

## Size and speed

The real size and search time are measured on your machine, not guessed: the
build prints the finished size, and `--ask` prints milliseconds per question.
If searches take more than about a second, tell me and we will tune it
(smaller `max_chars_per_article`, or a rebuild with fewer passages).
