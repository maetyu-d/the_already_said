#!/usr/bin/env python3
from __future__ import annotations

import csv
import gzip
import json
import shutil
import tarfile
import zipfile
from argparse import ArgumentParser
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from index_gutenberg_mirror import index_mirror


ROOT = Path(__file__).resolve().parent.parent
ARCHIVE_DIR = ROOT / "archive"
DOWNLOADS_DIR = ARCHIVE_DIR / "downloads"
CATALOGS_DIR = ARCHIVE_DIR / "catalogs"
TEXTS_DIR = ARCHIVE_DIR / "texts"
STATE_PATH = ARCHIVE_DIR / "ingest_state.json"
DEFAULT_DB_PATH = ROOT / "data" / "gutenberg.db"

BASE_FEEDS_URL = "https://www.gutenberg.org/cache/epub/feeds"
DEFAULT_CATALOG_URL = f"{BASE_FEEDS_URL}/pg_catalog.csv.gz"
DEFAULT_TEXT_ARCHIVE_URL = f"{BASE_FEEDS_URL}/txt-files.tar.zip"
CHUNK_SIZE = 1024 * 1024
TEXT_PATTERNS = (".txt", ".txt.utf-8", "-0.txt", "-8.txt")


def parse_args() -> ArgumentParser:
    parser = ArgumentParser(description="Download or import the Project Gutenberg text corpus and build a local index.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    download = subparsers.add_parser("download", help="Download Gutenberg catalog and text archive.")
    add_common_download_args(download)

    extract = subparsers.add_parser("extract", help="Extract the downloaded text archive into a local corpus.")
    extract.add_argument("--text-archive", type=Path, default=DOWNLOADS_DIR / "txt-files.tar.zip")
    extract.add_argument("--texts-dir", type=Path, default=TEXTS_DIR)
    extract.add_argument("--limit", type=int, help="Optional cap for extraction tests.")

    index = subparsers.add_parser("index", help="Build the SQLite index from a local extracted corpus.")
    index.add_argument("--texts-dir", type=Path, default=TEXTS_DIR)
    index.add_argument("--catalog", type=Path, default=CATALOGS_DIR / "pg_catalog.csv")
    index.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    index.add_argument("--chunk-size", type=int, default=950)
    index.add_argument("--overlap", type=int, default=180)
    index.add_argument("--limit", type=int)

    ingest = subparsers.add_parser("ingest", help="Download, extract, and index in one go.")
    add_common_download_args(ingest)
    ingest.add_argument("--texts-dir", type=Path, default=TEXTS_DIR)
    ingest.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    ingest.add_argument("--chunk-size", type=int, default=950)
    ingest.add_argument("--overlap", type=int, default=180)
    ingest.add_argument("--limit", type=int, help="Optional cap for extraction/index tests.")

    import_existing = subparsers.add_parser("import", help="Import an existing local dump without downloading.")
    import_existing.add_argument("--mirror-dir", type=Path, required=True)
    import_existing.add_argument("--catalog", type=Path)
    import_existing.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    import_existing.add_argument("--chunk-size", type=int, default=950)
    import_existing.add_argument("--overlap", type=int, default=180)
    import_existing.add_argument("--limit", type=int)

    return parser


def add_common_download_args(parser: ArgumentParser) -> None:
    parser.add_argument("--catalog-url", default=DEFAULT_CATALOG_URL)
    parser.add_argument("--text-archive-url", default=DEFAULT_TEXT_ARCHIVE_URL)
    parser.add_argument("--downloads-dir", type=Path, default=DOWNLOADS_DIR)
    parser.add_argument("--catalogs-dir", type=Path, default=CATALOGS_DIR)
    parser.add_argument("--force", action="store_true", help="Redownload files even if they already exist.")


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def update_state(**values) -> None:
    state = load_state()
    state.update(values)
    save_state(state)


def remote_file_size(url: str) -> int | None:
    request = Request(url, method="HEAD", headers={"User-Agent": "The-Already-Said/1.0"})
    try:
        with urlopen(request) as response:
            length = response.headers.get("Content-Length")
            return int(length) if length else None
    except (HTTPError, URLError, ValueError):
        return None


def stream_download(url: str, destination: Path, force: bool = False) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    remote_size = remote_file_size(url)
    existing_size = destination.stat().st_size if destination.exists() else 0

    headers = {"User-Agent": "The-Already-Said/1.0"}
    mode = "wb"
    if destination.exists() and not force and remote_size and existing_size == remote_size:
        print(f"Already downloaded: {destination}")
        return destination
    if destination.exists() and not force and existing_size > 0:
        headers["Range"] = f"bytes={existing_size}-"
        mode = "ab"

    request = Request(url, headers=headers)
    try:
        with urlopen(request) as response, destination.open(mode) as handle:
            downloaded = existing_size if mode == "ab" else 0
            last_reported_mib = downloaded // (64 * 1024 * 1024)
            while True:
                chunk = response.read(CHUNK_SIZE)
                if not chunk:
                    break
                handle.write(chunk)
                downloaded += len(chunk)
                current_reported_mib = downloaded // (64 * 1024 * 1024)
                if current_reported_mib == last_reported_mib and downloaded != remote_size:
                    continue
                last_reported_mib = current_reported_mib
                if remote_size:
                    pct = downloaded / remote_size * 100
                    print(f"{destination.name}: {downloaded // (1024 * 1024)} MiB / {remote_size // (1024 * 1024)} MiB ({pct:.1f}%)")
                else:
                    print(f"{destination.name}: {downloaded // (1024 * 1024)} MiB")
    except HTTPError as exc:
        if exc.code == 416 and destination.exists():
            return destination
        raise

    return destination


def unpack_catalog(catalog_archive: Path, catalogs_dir: Path) -> Path:
    catalogs_dir.mkdir(parents=True, exist_ok=True)
    target = catalogs_dir / "pg_catalog.csv"
    if catalog_archive.suffix == ".gz":
        with gzip.open(catalog_archive, "rb") as source, target.open("wb") as dest:
            shutil.copyfileobj(source, dest)
    else:
        shutil.copy2(catalog_archive, target)
    return target


def should_extract(member_name: str) -> bool:
    lower = member_name.lower()
    return any(lower.endswith(pattern) for pattern in TEXT_PATTERNS)


def extract_text_archive(text_archive: Path, texts_dir: Path, limit: int | None = None) -> int:
    texts_dir.mkdir(parents=True, exist_ok=True)
    extracted = 0
    with zipfile.ZipFile(text_archive) as outer_zip:
        inner_name = next((name for name in outer_zip.namelist() if name.endswith(".tar")), None)
        if inner_name is None:
            raise RuntimeError("Expected a .tar file inside txt-files.tar.zip")

        with outer_zip.open(inner_name) as inner_stream, tarfile.open(fileobj=inner_stream, mode="r|") as tar_handle:
            for member in tar_handle:
                if not member.isfile() or not should_extract(member.name):
                    continue
                relative = Path(member.name)
                target = texts_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                source = tar_handle.extractfile(member)
                if source is None:
                    continue
                with target.open("wb") as dest:
                    shutil.copyfileobj(source, dest, CHUNK_SIZE)
                extracted += 1
                if extracted % 500 == 0:
                    print(f"Extracted {extracted} text files...")
                if limit and extracted >= limit:
                    break
    print(f"Extracted {extracted} text files into {texts_dir}")
    return extracted


def load_catalog_rows(catalog_path: Path) -> int:
    with catalog_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return sum(1 for _ in reader)


def run_download(args) -> tuple[Path, Path]:
    downloads_dir = args.downloads_dir
    downloads_dir.mkdir(parents=True, exist_ok=True)

    catalog_archive_name = Path(args.catalog_url).name
    text_archive_name = Path(args.text_archive_url).name
    catalog_archive_path = stream_download(args.catalog_url, downloads_dir / catalog_archive_name, force=args.force)
    text_archive_path = stream_download(args.text_archive_url, downloads_dir / text_archive_name, force=args.force)

    catalog_path = unpack_catalog(catalog_archive_path, args.catalogs_dir)
    update_state(
        stage="downloaded",
        catalog_archive=str(catalog_archive_path),
        catalog_csv=str(catalog_path),
        text_archive=str(text_archive_path),
    )

    row_count = load_catalog_rows(catalog_path)
    print(f"Catalog ready at {catalog_path} with {row_count:,} rows")
    print(f"Text archive ready at {text_archive_path}")
    return catalog_path, text_archive_path


def run_extract(args) -> None:
    extracted = extract_text_archive(args.text_archive, args.texts_dir, limit=args.limit)
    update_state(stage="extracted", texts_dir=str(args.texts_dir), extracted_files=extracted)


def run_index(args) -> None:
    update_state(stage="indexing", texts_dir=str(args.texts_dir), db_path=str(args.db_path))
    index_mirror(
        mirror_dir=args.texts_dir,
        catalog_path=args.catalog,
        db_path=args.db_path,
        chunk_size=args.chunk_size,
        overlap=args.overlap,
        limit=args.limit,
        progress_callback=lambda payload: update_state(stage="indexing", **payload),
    )
    update_state(stage="complete", db_path=str(args.db_path))


def run_ingest(args) -> None:
    update_state(stage="starting", texts_dir=str(args.texts_dir), db_path=str(args.db_path))
    catalog_path, text_archive_path = run_download(args)
    extracted = extract_text_archive(text_archive_path, args.texts_dir, limit=args.limit)
    update_state(stage="indexing", texts_dir=str(args.texts_dir), db_path=str(args.db_path), extracted_files=extracted)
    index_mirror(
        mirror_dir=args.texts_dir,
        catalog_path=catalog_path,
        db_path=args.db_path,
        chunk_size=args.chunk_size,
        overlap=args.overlap,
        limit=args.limit,
        progress_callback=lambda payload: update_state(stage="indexing", **payload),
    )
    update_state(stage="complete", texts_dir=str(args.texts_dir), db_path=str(args.db_path), extracted_files=extracted)


def run_import(args) -> None:
    update_state(stage="indexing", texts_dir=str(args.mirror_dir), db_path=str(args.db_path))
    index_mirror(
        mirror_dir=args.mirror_dir,
        catalog_path=args.catalog,
        db_path=args.db_path,
        chunk_size=args.chunk_size,
        overlap=args.overlap,
        limit=args.limit,
        progress_callback=lambda payload: update_state(stage="indexing", **payload),
    )
    update_state(stage="complete", texts_dir=str(args.mirror_dir), db_path=str(args.db_path))


def main() -> None:
    parser = parse_args()
    args = parser.parse_args()

    if args.command == "download":
        run_download(args)
    elif args.command == "extract":
        run_extract(args)
    elif args.command == "index":
        run_index(args)
    elif args.command == "ingest":
        run_ingest(args)
    elif args.command == "import":
        run_import(args)
    else:
        parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
