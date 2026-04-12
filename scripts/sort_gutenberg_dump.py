#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import os
import shutil
from argparse import ArgumentParser
from pathlib import Path


TEXT_PATTERNS = (
    "**/*.txt",
    "**/*.txt.utf-8",
    "**/*-0.txt",
    "**/*-8.txt",
)


def parse_args() -> ArgumentParser:
    parser = ArgumentParser(description="Sort a raw local Gutenberg dump into a normalized local corpus.")
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--target-dir", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, help="Optional local pg_catalog.csv for rich metadata.")
    parser.add_argument("--mode", choices=("copy", "symlink", "hardlink"), default="symlink")
    parser.add_argument("--limit", type=int)
    return parser


def slugify(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-") or "untitled"


def discover_texts(source_dir: Path) -> list[Path]:
    seen: set[Path] = set()
    texts: list[Path] = []
    for pattern in TEXT_PATTERNS:
        for path in source_dir.glob(pattern):
            if path.is_file() and path not in seen:
                seen.add(path)
                texts.append(path)
    return sorted(texts)


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
                "year": (row.get("Release Date") or row.get("ReleaseDate") or "n.d.").strip(),
                "source_url": f"https://www.gutenberg.org/ebooks/{ebook_no}",
            }
    return records


def extract_ebook_no(path: Path) -> str | None:
    for part in reversed(path.parts):
        digits = "".join(ch for ch in part if ch.isdigit())
        if digits:
            return digits
    return None


def materialize(src: Path, dest: Path, mode: str) -> None:
    if dest.exists() or dest.is_symlink():
        dest.unlink()
    if mode == "copy":
        shutil.copy2(src, dest)
    elif mode == "hardlink":
        os.link(src, dest)
    else:
        dest.symlink_to(src.resolve())


def sort_dump(source_dir: Path, target_dir: Path, catalog_path: Path | None, mode: str, limit: int | None) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    catalog = load_catalog(catalog_path)
    texts = discover_texts(source_dir)
    if limit:
        texts = texts[:limit]

    manifest: list[dict] = []
    for path in texts:
        ebook_no = extract_ebook_no(path) or "unknown"
        metadata = catalog.get(
            ebook_no,
            {
                "title": path.stem,
                "author": "Unknown author",
                "year": "n.d.",
                "source_url": f"https://www.gutenberg.org/ebooks/{ebook_no}" if ebook_no != "unknown" else "",
            },
        )

        author_slug = slugify(metadata["author"])
        title_slug = slugify(metadata["title"])
        book_dir = target_dir / author_slug / f"{title_slug}-{ebook_no}"
        book_dir.mkdir(parents=True, exist_ok=True)

        text_dest = book_dir / "text.txt"
        json_dest = book_dir / "metadata.json"
        materialize(path, text_dest, mode)
        json_dest.write_text(
            json.dumps(
                {
                    "ebook_no": ebook_no,
                    "title": metadata["title"],
                    "author": metadata["author"],
                    "year": metadata["year"],
                    "source_url": metadata["source_url"],
                    "original_path": str(path),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        manifest.append(
            {
                "ebook_no": ebook_no,
                "author": metadata["author"],
                "title": metadata["title"],
                "path": str(book_dir),
            }
        )

    (target_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Sorted {len(manifest)} texts into {target_dir}")


if __name__ == "__main__":
    args = parse_args().parse_args()
    sort_dump(args.source_dir, args.target_dir, args.catalog, args.mode, args.limit)
