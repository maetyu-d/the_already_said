#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sqlite3
from argparse import ArgumentParser
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORPUS_DIR = ROOT / "corpus"
DEFAULT_DATA_DIR = ROOT / "data"
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "gutenberg.db"
DEFAULT_CHUNK_SIZE = 950
DEFAULT_OVERLAP = 180

WORD_RE = re.compile(r"\S+")
HEADER_RE = re.compile(
    r"\*\*\* START OF THE PROJECT GUTENBERG EBOOK .*?\*\*\*",
    re.IGNORECASE | re.DOTALL,
)
FOOTER_RE = re.compile(
    r"\*\*\* END OF THE PROJECT GUTENBERG EBOOK .*",
    re.IGNORECASE | re.DOTALL,
)


def iter_books(corpus_dir: Path) -> list[tuple[Path, dict]]:
    books: list[tuple[Path, dict]] = []
    for text_path in sorted(corpus_dir.glob("*.txt")):
        meta_path = text_path.with_suffix(".json")
        if not meta_path.exists():
            continue
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        books.append((text_path, metadata))
    return books


def trim_gutenberg_boilerplate(text: str) -> str:
    text = HEADER_RE.sub("", text)
    text = FOOTER_RE.sub("", text)
    return text.strip()


def chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    words = WORD_RE.findall(text)
    chunks: list[str] = []
    start = 0
    while start < len(words):
        piece = " ".join(words[start : start + chunk_size]).strip()
        if piece:
            chunks.append(piece)
        start += chunk_size - overlap
    return chunks


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = NORMAL;
        PRAGMA temp_store = MEMORY;
        PRAGMA mmap_size = 268435456;
        """
    )
    conn.executescript(
        """
        DROP TABLE IF EXISTS books;
        DROP TABLE IF EXISTS passages;
        DROP TABLE IF EXISTS passages_fts;

        CREATE TABLE books (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            author TEXT NOT NULL,
            year TEXT NOT NULL,
            source_url TEXT NOT NULL,
            text_path TEXT NOT NULL
        );

        CREATE TABLE passages (
            id INTEGER PRIMARY KEY,
            book_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            author TEXT NOT NULL,
            year TEXT NOT NULL,
            source_url TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            FOREIGN KEY(book_id) REFERENCES books(id)
        );

        CREATE VIRTUAL TABLE passages_fts USING fts5(
            text,
            content='passages',
            content_rowid='id',
            tokenize='porter unicode61'
        );

        CREATE INDEX idx_passages_book_id ON passages(book_id);
        """
    )


def build(corpus_dir: Path, db_path: Path, chunk_size: int, overlap: int) -> None:
    db_path.parent.mkdir(exist_ok=True)
    books = iter_books(corpus_dir)
    conn = sqlite3.connect(db_path)
    try:
        init_db(conn)
        indexed_books = 0
        indexed_passages = 0
        total_books = len(books)
        for text_path, metadata in books:
            text = trim_gutenberg_boilerplate(text_path.read_text(encoding="utf-8"))
            book_cursor = conn.execute(
                """
                INSERT INTO books (title, author, year, source_url, text_path)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    metadata.get("title", text_path.stem),
                    metadata.get("author", "Unknown author"),
                    metadata.get("year", "n.d."),
                    metadata.get("source_url", ""),
                    str(text_path),
                ),
            )
            book_id = book_cursor.lastrowid
            for index, chunk in enumerate(chunk_text(text, chunk_size, overlap)):
                cursor = conn.execute(
                    """
                    INSERT INTO passages (book_id, title, author, year, source_url, chunk_index, text)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        book_id,
                        metadata.get("title", text_path.stem),
                        metadata.get("author", "Unknown author"),
                        metadata.get("year", "n.d."),
                        metadata.get("source_url", ""),
                        index,
                        chunk,
                    ),
                )
                row_id = cursor.lastrowid
                conn.execute(
                    "INSERT INTO passages_fts(rowid, text) VALUES (?, ?)",
                    (row_id, chunk),
                )
                indexed_passages += 1
            indexed_books += 1
            if indexed_books % 100 == 0:
                conn.commit()
                conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                percent = (indexed_books / total_books * 100) if total_books else 100.0
                print(
                    f"Indexed {indexed_books:,}/{total_books:,} books "
                    f"({percent:.1f}%), {indexed_passages:,} passages"
                )
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()

    print(f"Indexed {len(books)} books into {db_path}")


def parse_args() -> ArgumentParser:
    parser = ArgumentParser(description="Build a local SQLite FTS index from Gutenberg text files.")
    parser.add_argument("--corpus-dir", type=Path, default=DEFAULT_CORPUS_DIR)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--overlap", type=int, default=DEFAULT_OVERLAP)
    return parser


if __name__ == "__main__":
    args = parse_args().parse_args()
    build(args.corpus_dir, args.db_path, args.chunk_size, args.overlap)
