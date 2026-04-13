# The Already Said

A split-screen writing app inspired by the quiet focus of [iA Writer](https://ia.net/writer), but where a second pane attempts to (re)compose your text entirely out of quotations found in a locally indexed Project Gutenberg corpus. A third, full screen view enables a typeset version of the result to be exported.

<img width="1552" height="919" alt="Screenshot 2026-04-13 at 20 57 09" src="https://github.com/user-attachments/assets/7e6e80b9-dd59-4dee-9dd1-819ba408f170" />

<img width="1552" height="919" alt="Screenshot 2026-04-13 at 20 57 14" src="https://github.com/user-attachments/assets/1b649e3a-02c7-4208-bb08-6a1827f97835" />


## What it does

- Left side: a distraction-light writing surface.
- Right side: a quotation-only rewrite assembled from your text by searching a local SQLite FTS5 index.
- References: Harvard inline citations or Oxford-style notes.
- Corpus: stored locally on disk, not in the browser.

## Why this architecture

The expensive part here is not the editor UI, it is searching a very large archive quickly. For that reason the project uses:

- Python's built-in `sqlite3` with FTS5 for fast local full-text search.
- A small HTTP server for the UI and API.
- Chunked passages instead of entire books, which keeps search results relevant and memory use low.
- Boilerplate trimming during indexing so Gutenberg headers and footers do not pollute results.

This is a better fit for a full local Gutenberg corpus than loading everything into client-side JavaScript.

## Run it in the browser

```bash
python3 scripts/build_index.py
python3 app.py
```

Then open `http://127.0.0.1:8000`.

## Run it as a desktop app

```bash
python3 desktop_app.py
```

This launches a native macOS window with the draft on the left and the quotation rewrite on the right, without needing a browser tab. In development it will use `data/gutenberg.db` automatically when present.

## Corpus format

Put local text files in [`corpus`](/Users/user/Documents/Gutenberg/corpus) with a matching JSON sidecar:

```text
corpus/
  moby_dick.txt
  moby_dick.json
```

Example metadata:

```json
{
  "title": "Moby-Dick; or, The Whale",
  "author": "Herman Melville",
  "year": "1851",
  "source_url": "https://www.gutenberg.org/ebooks/2701"
}
```

## Building a larger local Gutenberg index

If you have a local directory of Gutenberg `.txt` files and matching metadata sidecars, you can point the indexer at it directly:

```bash
python3 scripts/build_index.py --corpus-dir /path/to/your/gutenberg-texts --db-path /path/to/your/gutenberg.db
```

Tuning options:

- `--chunk-size`: larger chunks preserve more context.
- `--overlap`: overlapping chunks reduce hard cutoffs between passages.

For a very large corpus, keeping the SQLite database on a fast local SSD will matter more than adding a frontend build chain.

## Indexing a real local Gutenberg mirror

If you already have a local Gutenberg mirror or dump, you do not need to copy everything into `corpus/` first. Use the direct mirror indexer:

```bash
python3 scripts/index_gutenberg_mirror.py \
  --mirror-dir /path/to/gutenberg/texts \
  --catalog /path/to/pg_catalog.csv \
  --db-path /path/to/gutenberg.db
```

Notes:

- `--catalog` is optional but recommended because it fills in titles and authors from Gutenberg's catalog.
- The script looks for common Gutenberg plain-text filename patterns recursively.
- If you want a small test run first, add `--limit 500`.
- The database is configured with `WAL`, in-memory temp storage, and a larger mmap region to keep local search responsive.

## Full Gutenberg ingest pipeline

The project now includes an ingest script that can download the official Project Gutenberg weekly catalog and the full plain-text archive, extract it locally, and build the SQLite search index.

Official sources:

- `pg_catalog.csv.gz`
- `txt-files.tar.zip`

Run the full pipeline:

```bash
python3 scripts/ingest_gutenberg.py ingest
```

Useful staged commands:

```bash
python3 scripts/ingest_gutenberg.py download
python3 scripts/ingest_gutenberg.py extract
python3 scripts/ingest_gutenberg.py index
python3 scripts/ingest_gutenberg.py import --mirror-dir /path/to/existing/gutenberg
```

Notes:

- Downloads are resumable when the remote server supports ranged requests.
- The full text archive is large, around 10 GB compressed and much larger once extracted.
- For a smaller shakeout run, use `--limit` on `extract`, `index`, or `ingest`.
- Progress and chosen paths are recorded in `archive/ingest_state.json`.

## Sorting a raw Gutenberg dump

If your dump is messy and you want a cleaner local corpus layout first, sort it into a normalized author/title tree:

```bash
python3 scripts/sort_gutenberg_dump.py \
  --source-dir /path/to/raw-gutenberg \
  --target-dir /path/to/sorted-gutenberg \
  --catalog /path/to/pg_catalog.csv \
  --mode symlink
```

This creates a stable local structure like:

```text
sorted-gutenberg/
  jane-austen/
    pride-and-prejudice-1342/
      text.txt
      metadata.json
```

`--mode symlink` is fastest and saves disk space. Use `copy` if you want a fully materialized archive.

## Packaging for desktop

The repo includes a PyInstaller spec for a macOS `.app` bundle:

```bash
python3 -m PyInstaller AlreadySaid.spec
```

That produces a lightweight desktop app bundle in `dist/` with the UI assets only. The standalone app expects the large Gutenberg index to live outside the `.app`, and on first launch it will ask you to choose your external `gutenberg.db` file if it cannot find a saved location.

## Repo and release scope

The Git repository and lightweight release zip are intended to contain the app, scripts, and sample seed files only.

- The full downloaded Gutenberg archive is not committed.
- The extracted full-text corpus is not committed.
- The large SQLite search index is not committed.
- The packaged `.app` is intentionally kept separate from the large local database.

If you want to distribute the full corpus separately, host the archive or index outside the repo and point users at the ingest workflow.

## Current limitations

- The quotation composer currently chooses the best-matching sentence or two from the best-matching indexed passage for each input sentence.
- It assumes plain-text Gutenberg files already exist locally.
- It does not yet crawl Project Gutenberg metadata for you automatically.
