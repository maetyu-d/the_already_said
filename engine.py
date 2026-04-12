from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from html import escape
from pathlib import Path


if getattr(sys, "frozen", False):
    ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
else:
    ROOT = Path(__file__).resolve().parent
ENV_DB_PATH = "ALREADY_SAID_DB_PATH"
APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "The Already Said"
CONFIG_PATH = APP_SUPPORT_DIR / "config.json"
DEV_DB_PATH = Path(__file__).resolve().parent / "data" / "gutenberg.db"
BUNDLED_DB_PATH = ROOT / "data" / "gutenberg.db"

WORD_RE = re.compile(r"[a-zA-Z']+")
SENTENCE_RE = re.compile(r"[^.!?\n]+[.!?]?")
INNER_QUOTE_RE = re.compile(r"[\"“]([^\"”]{24,})[\"”]")
LEADING_ARTIFACT_RE = re.compile(r"^[\]\[\d\s:;,.!?-]+")
COMMENTARY_TITLE_HINTS = (
    "complete works",
    "project gutenberg works",
    "study",
    "masters",
    "masterpieces",
    "history",
    "works",
    "novel",
    "novelists",
    "essays",
    "introduction",
    "criticism",
    "linked index",
)
STOPWORDS = {
    "about", "after", "again", "against", "almost", "also", "among", "because", "been", "before",
    "being", "between", "could", "every", "first", "from", "good", "have", "into", "little",
    "many", "more", "most", "much", "must", "never", "nothing", "other", "over", "same",
    "should", "since", "some", "such", "than", "that", "their", "them", "then", "there",
    "these", "they", "this", "those", "through", "upon", "very", "want", "were", "what",
    "when", "where", "which", "while", "with", "would", "your", "wife", "man",
    "single", "have", "just", "been", "well", "into", "part",
}


@dataclass
class SearchResult:
    title: str
    author: str
    year: str
    source_url: str
    text: str
    score: float


def resolve_db_path(db_path: Path | None = None) -> Path:
    if db_path is not None:
        return Path(db_path)

    env_value = os.environ.get(ENV_DB_PATH)
    if env_value:
        return Path(env_value).expanduser()

    if CONFIG_PATH.exists():
        try:
            payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        configured = payload.get("db_path")
        if configured:
            return Path(configured).expanduser()

    if DEV_DB_PATH.exists():
        return DEV_DB_PATH

    return BUNDLED_DB_PATH


def tokenize(text: str) -> list[str]:
    return [word.lower() for word in WORD_RE.findall(text)]


def keyword_candidates(text: str, max_terms: int = 8) -> list[str]:
    seen: list[str] = []
    for token in tokenize(text):
        if len(token) < 4 or token in STOPWORDS or token in seen:
            continue
        seen.append(token)
    seen.sort(key=len, reverse=True)
    return seen[:max_terms]


def phrase_candidates(text: str) -> list[str]:
    tokens = tokenize(text)
    if len(tokens) < 4:
        return []

    phrases: list[str] = []
    max_window = min(8, len(tokens))
    min_window = min(4, max_window)
    for window in range(max_window, min_window - 1, -1):
        for start in range(0, len(tokens) - window + 1):
            phrase = " ".join(tokens[start : start + window])
            if len(set(tokens[start : start + window])) < max(3, window - 1):
                continue
            if phrase not in phrases:
                phrases.append(phrase)
        if phrases:
            break
    return [f'"{phrase}"' for phrase in phrases[:4]]


def query_candidates(text: str) -> list[str]:
    keywords = keyword_candidates(text)
    phrases = phrase_candidates(text)
    if not keywords and not phrases:
        return []

    queries: list[str] = list(phrases)
    if len(keywords) >= 4:
        queries.append(" AND ".join(keywords[:4]))
    if len(keywords) >= 3:
        queries.append(" AND ".join(keywords[:3]))
    if len(keywords) >= 2:
        queries.append(" AND ".join(keywords[:2]))
    queries.append(" OR ".join(keywords[:3]))

    deduped: list[str] = []
    for query in queries:
        if query and query not in deduped:
            deduped.append(query)
    return deduped


def split_sentences(text: str) -> list[str]:
    return [chunk.strip() for chunk in SENTENCE_RE.findall(text) if chunk.strip()]


def source_preference(result: SearchResult, query_text: str) -> float:
    title = (result.title or "").lower()
    author = (result.author or "").lower()
    score = 0.0
    if any(hint in title for hint in COMMENTARY_TITLE_HINTS):
        score -= 1.25
    if "[editor]" in author or "[translator]" in author:
        score -= 0.35
    if len(title) < 40:
        score += 0.2
    if "\n" in result.title:
        score -= 0.25
    if query_text.lower() in result.text.lower():
        score += 2.0
    return score


def fetch_results(query: str, limit: int = 8, db_path: Path | None = None) -> list[SearchResult]:
    db_path = resolve_db_path(db_path)
    if not db_path.exists():
        return []

    candidates = query_candidates(query)
    if not candidates:
        return []

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        scored_rows: dict[tuple[str, str, str, str, str], SearchResult] = {}
        for candidate in candidates:
            rows = conn.execute(
                """
                SELECT
                    passages.title,
                    passages.author,
                    passages.year,
                    passages.source_url,
                    passages.text,
                    bm25(passages_fts, 1.0, 0.3) AS score
                FROM passages_fts
                JOIN passages ON passages_fts.rowid = passages.id
                WHERE passages_fts MATCH ?
                ORDER BY score
                LIMIT 12
                """,
                (candidate,),
            ).fetchall()
            for row in rows:
                result = SearchResult(
                    title=row["title"],
                    author=row["author"],
                    year=row["year"],
                    source_url=row["source_url"],
                    text=row["text"],
                    score=row["score"],
                )
                key = (result.title, result.author, result.year, result.source_url, result.text)
                reranked = (
                    sentence_score(query, result.text[:2200])
                    + source_preference(result, query)
                    + max(0.0, 0.05 - result.score)
                )
                existing = scored_rows.get(key)
                if existing is None or reranked > existing.score:
                    result.score = reranked
                    scored_rows[key] = result
            if scored_rows:
                break
    finally:
        conn.close()

    return sorted(scored_rows.values(), key=lambda item: item.score, reverse=True)[:limit]


def sentence_score(segment: str, candidate: str) -> float:
    segment_tokens = set(tokenize(segment))
    candidate_tokens = set(tokenize(candidate))
    if not segment_tokens or not candidate_tokens:
        return 0.0

    overlap = len(segment_tokens & candidate_tokens)
    density = overlap / max(len(candidate_tokens), 1)
    coverage = overlap / max(len(segment_tokens), 1)
    return coverage * 2 + density


def best_quote(segment: str, result: SearchResult) -> str:
    sentences = split_sentences(result.text)
    if not sentences:
        return result.text.strip()

    scored = sorted(
        ((sentence_score(segment, sentence), sentence) for sentence in sentences),
        key=lambda item: item[0],
        reverse=True,
    )
    top_score, top_sentence = scored[0]
    if top_score <= 0:
        return sentences[0].strip()

    if len(scored) > 1 and scored[1][0] > top_score * 0.6:
        return clean_quote_text(f"{top_sentence.strip()} {scored[1][1].strip()}".strip())

    return clean_quote_text(top_sentence.strip())


def clean_quote_text(text: str) -> str:
    cleaned = LEADING_ARTIFACT_RE.sub("", text).strip()
    return cleaned


def extract_inner_quote(text: str) -> str | None:
    match = INNER_QUOTE_RE.search(text)
    if not match:
        return None
    return match.group(1).strip()


def refine_primary_source(segment: str, quote: str, result: SearchResult, db_path: Path | None) -> tuple[str, SearchResult]:
    db_path = resolve_db_path(db_path)
    inner = extract_inner_quote(quote)
    if not inner:
        return quote, result

    candidates = fetch_results(inner, limit=5, db_path=db_path)
    if not candidates:
        return quote, result

    preferred = sorted(
        candidates,
        key=lambda candidate: (
            sentence_score(inner, candidate.text[:2200]) + source_preference(candidate, inner),
            -len(candidate.title),
        ),
        reverse=True,
    )[0]
    refined_quote = best_quote(inner, preferred)
    return refined_quote, preferred


def harvard_citation(result: SearchResult) -> str:
    author = result.author or "Unknown author"
    year = result.year or "n.d."
    title = result.title or "Untitled"
    return f"({author}, {year}, {title})"


def oxford_note(result: SearchResult, index: int) -> tuple[str, str]:
    author = result.author or "Unknown author"
    title = result.title or "Untitled"
    year = result.year or "n.d."
    marker = f"<sup>{index}</sup>"
    note = f"{index}. {author}, <em>{escape(title)}</em> ({year}), Project Gutenberg."
    return marker, note


def compose_quotation_text(text: str, style: str, db_path: Path | None = None) -> dict:
    db_path = resolve_db_path(db_path)
    segments = split_sentences(text)
    if not segments:
        return {
            "html": "<p class='empty'>Start writing on the left. Quotations will gather here.</p>",
            "matches": [],
            "notes": [],
        }

    fragments: list[str] = []
    matches: list[dict] = []
    notes: list[str] = []

    for segment in segments:
        result = next(iter(fetch_results(segment, limit=1, db_path=db_path)), None)
        if result is None:
            fragments.append(
                "<p class='missing'>No quotation found for this passage yet. Index more texts to deepen the archive.</p>"
            )
            continue

        quote = best_quote(segment, result)
        quote, result = refine_primary_source(segment, quote, result, db_path)
        if style == "oxford":
            marker, note = oxford_note(result, len(notes) + 1)
            citation_html = marker
            notes.append(note)
        else:
            citation_html = f" <span class='citation'>{escape(harvard_citation(result))}</span>"

        fragments.append(
            "<p><span class='quote-mark'>&ldquo;</span>"
            f"{escape(quote)}"
            "<span class='quote-mark'>&rdquo;</span>"
            f"{citation_html}</p>"
        )
        matches.append(
            {
                "input": segment,
                "quote": quote,
                "title": result.title,
                "author": result.author,
                "year": result.year,
                "sourceUrl": result.source_url,
            }
        )

    if style == "oxford" and notes:
        fragments.append(
            "<section class='notes'><h3>Notes</h3>"
            + "".join(f"<p>{note}</p>" for note in notes)
            + "</section>"
        )

    return {"html": "".join(fragments), "matches": matches, "notes": notes}


def compose_plaintext(text: str, style: str, db_path: Path | None = None) -> dict:
    db_path = resolve_db_path(db_path)
    payload = compose_quotation_text(text, style, db_path=db_path)
    blocks: list[str] = []
    for match_index, match in enumerate(payload["matches"], start=1):
        if style == "oxford":
            citation = f"[{match_index}]"
        else:
            citation = harvard_citation(
                SearchResult(
                    title=match["title"],
                    author=match["author"],
                    year=match["year"],
                    source_url=match["sourceUrl"],
                    text=match["quote"],
                    score=0.0,
                )
            )
        blocks.append(f"\"{match['quote']}\" {citation}")

    notes = []
    if style == "oxford":
        for index, match in enumerate(payload["matches"], start=1):
            notes.append(
                f"{index}. {match['author'] or 'Unknown author'}, {match['title'] or 'Untitled'} ({match['year'] or 'n.d.'}), Project Gutenberg."
            )

    return {"text": "\n\n".join(blocks + (["\nNotes\n" + "\n".join(notes)] if notes else [])), "matches": payload["matches"]}


def stats(db_path: Path | None = None) -> dict:
    db_path = resolve_db_path(db_path)
    if not db_path.exists():
        return {"indexed_passages": 0, "indexed_books": 0}

    conn = sqlite3.connect(db_path)
    try:
        passages = conn.execute("SELECT COUNT(*) FROM passages").fetchone()[0]
        books = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    finally:
        conn.close()
    return {"indexed_passages": passages, "indexed_books": books}


def export_matches_json(text: str, style: str, db_path: Path | None = None) -> str:
    db_path = resolve_db_path(db_path)
    payload = compose_quotation_text(text, style, db_path=db_path)
    return json.dumps(payload, indent=2)
