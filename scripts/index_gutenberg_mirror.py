#!/usr/bin/env python3
from __future__ import annotations

import csv
import re
import sqlite3
from argparse import ArgumentParser
from pathlib import Path
from typing import Callable

from build_index import chunk_text, init_db, trim_gutenberg_boilerplate


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = ROOT / "data" / "gutenberg.db"
DEFAULT_CHUNK_SIZE = 950
DEFAULT_OVERLAP = 180

TEXT_PATTERNS = (
    "**/*.txt",
    "**/*.txt.utf-8",
    "**/*-0.txt",
    "**/*-8.txt",
)
YEAR_RE = re.compile(r"\b(1[6-9]\d{2}|20\d{2})\b")


def parse_args() -> ArgumentParser:
    parser = ArgumentParser(description="Index a local Project Gutenberg mirror directly into SQLite FTS5.")
    parser.add_argument("--mirror-dir", type=Path, required=True, help="Directory containing Gutenberg text files.")
    parser.add_argument("--catalog", type=Path, help="Optional local pg_catalog.csv for richer metadata.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--overlap", type=int, default=DEFAULT_OVERLAP)
    parser.add_argument("--limit", type=int, help="Optional cap for test runs.")
    parser.add_argument("--batch-books", type=int, default=250, help="Commit and checkpoint every N indexed books.")
    return parser


def load_catalog(catalog_path: Path | None) -> dict[str, dict]:
    if not catalog_path or not catalog_path.exists():
        return {}

    records: dict[str, dict] = {}
    with catalog_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            ebook_no = (row.get("Text#", "") or row.get("ID", "")).strip()
            if not ebook_no:
                continue
            records[ebook_no] = {
                "title": (row.get("Title") or "").strip() or f"Gutenberg #{ebook_no}",
                "author": (row.get("Authors") or row.get("Author") or "Unknown author").strip(),
                "year": extract_year(row.get("Release Date", "") or row.get("ReleaseDate", "")),
                "source_url": f"https://www.gutenberg.org/ebooks/{ebook_no}",
            }
    return records


def extract_year(value: str) -> str:
    match = YEAR_RE.search(value or "")
    return match.group(1) if match else "n.d."


def extract_ebook_no(path: Path) -> str | None:
    for part in reversed(path.parts):
        digits = "".join(ch for ch in part if ch.isdigit())
        if digits:
            return digits
    return None


def discover_texts(mirror_dir: Path) -> list[Path]:
    seen: set[Path] = set()
    files: list[Path] = []
    for pattern in TEXT_PATTERNS:
        for path in mirror_dir.glob(pattern):
            if path.is_file() and path not in seen:
                seen.add(path)
                files.append(path)
    return sorted(files)


def metadata_for(path: Path, catalog: dict[str, dict]) -> dict:
    ebook_no = extract_ebook_no(path)
    if ebook_no and ebook_no in catalog:
        return catalog[ebook_no]

    return {
        "title": path.stem,
        "author": "Unknown author",
        "year": "n.d.",
        "source_url": f"https://www.gutenberg.org/ebooks/{ebook_no}" if ebook_no else "",
    }


def index_mirror(
    mirror_dir: Path,
    catalog_path: Path | None,
    db_path: Path,
    chunk_size: int,
    overlap: int,
    limit: int | None,
    batch_books: int = 250,
    progress_callback: Callable[[dict], None] | None = None,
) -> None:
    catalog = load_catalog(catalog_path)
    texts = discover_texts(mirror_dir)
    if limit:
        texts = texts[:limit]

    db_path.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        init_db(conn)
        indexed_books = 0
        indexed_passages = 0
        total_books = len(texts)

        def flush_progress(force_checkpoint: bool = False) -> None:
            conn.commit()
            checkpoint_mode = "TRUNCATE" if force_checkpoint else "PASSIVE"
            conn.execute(f"PRAGMA wal_checkpoint({checkpoint_mode})")
            if progress_callback is not None:
                progress_callback(
                    {
                        "indexed_books": indexed_books,
                        "indexed_passages": indexed_passages,
                        "total_books": total_books,
                        "db_path": str(db_path),
                    }
                )
            percent = (indexed_books / total_books * 100) if total_books else 100.0
            print(
                f"Indexed {indexed_books:,}/{total_books:,} books "
                f"({percent:.1f}%), {indexed_passages:,} passages"
            )

        for text_path in texts:
            raw_text = text_path.read_text(encoding="utf-8", errors="ignore")
            text = trim_gutenberg_boilerplate(raw_text)
            if not text:
                continue

            metadata = metadata_for(text_path, catalog)
            cursor = conn.execute(
                """
                INSERT INTO books (title, author, year, source_url, text_path)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    metadata["title"],
                    metadata["author"],
                    metadata["year"],
                    metadata["source_url"],
                    str(text_path),
                ),
            )
            book_id = cursor.lastrowid

            for index, chunk in enumerate(chunk_text(text, chunk_size, overlap)):
                row = conn.execute(
                    """
                    INSERT INTO passages (book_id, title, author, year, source_url, chunk_index, text)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        book_id,
                        metadata["title"],
                        metadata["author"],
                        metadata["year"],
                        metadata["source_url"],
                        index,
                        chunk,
                    ),
                )
                conn.execute(
                    "INSERT INTO passages_fts(rowid, text) VALUES (?, ?)",
                    (row.lastrowid, chunk),
                )
                indexed_passages += 1

            indexed_books += 1
            if indexed_books % batch_books == 0:
                flush_progress()

        flush_progress(force_checkpoint=True)
    finally:
        conn.close()

    print(f"Indexed {indexed_books} books and {indexed_passages} passages into {db_path}")


if __name__ == "__main__":
    args = parse_args().parse_args()
    index_mirror(
        mirror_dir=args.mirror_dir,
        catalog_path=args.catalog,
        db_path=args.db_path,
        chunk_size=args.chunk_size,
        overlap=args.overlap,
        limit=args.limit,
        batch_books=args.batch_books,
    )
