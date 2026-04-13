from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
from difflib import SequenceMatcher
from dataclasses import dataclass
from functools import lru_cache
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
INNER_QUOTE_RE = re.compile(r"[\"“]([^\"”]{6,})[\"”]")
LEADING_ARTIFACT_RE = re.compile(r"^[\]\[\d\s:;,.!?-]+")
LEADING_HEADING_RE = re.compile(r"^(?:[A-Z][A-Z' -]{3,}\s+){1,3}")
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
    "anthology",
    "dictionary",
    "reference",
    "manual",
    "encyclopedia",
    "collection",
    "miscellany",
    "needlecraft",
    "copyright renewals",
    "american thought",
    "weaver of fantasy",
    "biography",
    "letters of",
    "memoir",
    "punchinello",
    "volume",
    "no.",
    "magazine",
    "review",
    "journal",
    "weekly",
    "medical",
    "investigation",
    "architecture",
    "century",
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
PRIMARY_QUERY_TIMEOUT_SECONDS = 6.0
FALLBACK_AFTER_SECONDS = 150.0
SQLITE_PROGRESS_STEPS = 20_000
SHORT_QUERY_TOKEN_LIMIT = 4
LONG_QUERY_TOKEN_THRESHOLD = 10
LONG_QUERY_WINDOW = 5


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


def normalized_text(text: str) -> str:
    return " ".join(tokenize(text))


def token_like_pattern(text: str) -> str:
    tokens = tokenize(text)
    if not tokens:
        return "%"
    return "%" + "%".join(tokens) + "%"


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
    if len(tokens) < 2:
        return []

    phrases: list[str] = []
    max_window = min(8, len(tokens))
    min_window = 2 if len(tokens) <= 3 else min(4, max_window)
    for window in range(max_window, min_window - 1, -1):
        for start in range(0, len(tokens) - window + 1):
            phrase = " ".join(tokens[start : start + window])
            if len(set(tokens[start : start + window])) < max(2, window - 1):
                continue
            if phrase not in phrases:
                phrases.append(phrase)
        if phrases:
            break
    return [f'"{phrase}"' for phrase in phrases[:4]]


def recovery_phrase_queries(text: str) -> list[str]:
    tokens = tokenize(text)
    if len(tokens) < 5:
        return []

    phrases: list[str] = []
    clause_chunks = re.split(r"[,:;.!?]+", text)
    for chunk in clause_chunks:
        clause_tokens = tokenize(chunk)
        if 4 <= len(clause_tokens) <= 9:
            phrase = " ".join(clause_tokens).strip()
            if phrase and phrase not in phrases:
                phrases.append(phrase)

    windows: list[list[str]] = []
    windows.append(tokens[: min(6, len(tokens))])
    windows.append(tokens[-min(6, len(tokens)) :])
    if len(tokens) > 10:
        middle_start = max(0, (len(tokens) // 2) - 2)
        windows.append(tokens[middle_start : middle_start + 6])

    for window in windows:
        phrase = " ".join(window).strip()
        if phrase and phrase not in phrases:
            phrases.append(phrase)
    return phrases


def query_candidates(text: str) -> list[str]:
    tokens = tokenize(text)
    keywords = keyword_candidates(text)
    phrases = phrase_candidates(text)
    if not keywords and not phrases and not tokens:
        return []

    queries: list[str] = []
    is_long_query = len(tokens) >= LONG_QUERY_TOKEN_THRESHOLD
    if len(tokens) >= 2 and not is_long_query:
        queries.append(f'"{" ".join(tokens)}"')
    queries.extend(phrases)
    if len(tokens) >= 5:
        queries.append(f'"{" ".join(tokens[:5])}"')
        queries.append(f'"{" ".join(tokens[-5:])}"')
    if len(tokens) >= 6:
        queries.append(f'"{" ".join(tokens[:3])}" AND "{" ".join(tokens[-3:])}"')
    if is_long_query:
        middle_start = max(0, (len(tokens) // 2) - (LONG_QUERY_WINDOW // 2))
        middle = tokens[middle_start : middle_start + LONG_QUERY_WINDOW]
        if len(middle) >= 3:
            queries.append(f'"{" ".join(middle)}"')
        if keywords:
            queries.append(" AND ".join(keywords[: min(3, len(keywords))]))
    if len(tokens) == 1:
        queries.append(tokens[0])
        queries.append(f"{tokens[0]}*")
    elif len(tokens) <= 3:
        queries.append(" AND ".join(tokens))
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
    source_url = (result.source_url or "").lower()
    score = 0.0
    if any(hint in title for hint in COMMENTARY_TITLE_HINTS):
        score -= 1.75
    if "[editor]" in author or "[translator]" in author:
        score -= 0.35
    if "various" in author:
        score -= 0.9
    if len(title) < 36:
        score += 0.25
    if re.search(r"\b(or,|volume|vol\.|complete|works)\b", title):
        score -= 0.2
    if "\n" in result.title:
        score -= 0.25
    if normalized_text(query_text) in normalized_text(result.text):
        score += 2.5
    if "/ebooks/" in source_url:
        score += 0.15
        match = re.search(r"/ebooks/(\d+)", source_url)
        if match:
            try:
                ebook_id = int(match.group(1))
            except ValueError:
                ebook_id = 0
            if ebook_id:
                score += max(0.0, 1.2 - min(ebook_id, 50000) / 50000 * 1.2)
    return score


def quotation_context_penalty(result: SearchResult, query_text: str) -> float:
    text = (result.text or "").lower()
    normalized_query = normalized_text(query_text)
    penalty = 0.0
    if not normalized_query:
        return penalty
    if any(marker in text for marker in ("reads:", "read:", "quoted in", "quotes", "quote:", "quotation")):
        penalty -= 0.9
    if any(marker in text for marker in (
        "_moby dick_",
        "_moby-dick_",
        "_a tale of two cities_",
        "_pride and prejudice_",
        "“call me ishmael",
        "\"call me ishmael",
        "it was the best of times",
    )):
        penalty -= 0.45
    return penalty


def is_secondary_source(result: SearchResult) -> bool:
    title = (result.title or "").lower()
    author = (result.author or "").lower()
    return any(hint in title for hint in COMMENTARY_TITLE_HINTS) or "[editor]" in author or "[translator]" in author


def connect_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA mmap_size=268435456")
    return conn


def thaw_result_rows(rows: tuple[tuple[str, str, str, str, str, float], ...]) -> list[SearchResult]:
    return [
        SearchResult(
            title=title,
            author=author,
            year=year,
            source_url=source_url,
            text=text,
            score=score,
        )
        for title, author, year, source_url, text, score in rows
    ]


def execute_with_timeout(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple,
    timeout_seconds: float,
) -> list[sqlite3.Row]:
    deadline = time.monotonic() + timeout_seconds

    def progress_handler() -> int:
        return 1 if time.monotonic() > deadline else 0

    conn.set_progress_handler(progress_handler, SQLITE_PROGRESS_STEPS)
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as error:
        if "interrupted" in str(error).lower():
            return []
        raise
    finally:
        conn.set_progress_handler(None, 0)


def rerank_result(query: str, result: SearchResult) -> float:
    return (
        sentence_score(query, result.text[:2200])
        + exact_phrase_score(query, result.text[:2200])
        + sequence_score(query, result.text[:2200])
        + source_preference(result, query)
        + quotation_context_penalty(result, query)
        + max(0.0, 0.05 - result.score)
    )


def collect_scored_rows(
    conn: sqlite3.Connection,
    query: str,
    candidates: list[str],
    timeout_seconds: float,
    row_limit: int,
) -> dict[tuple[str, str, str, str, str], SearchResult]:
    scored_rows: dict[tuple[str, str, str, str, str], SearchResult] = {}
    for candidate in candidates:
        rows = execute_with_timeout(
            conn,
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
            LIMIT ?
            """,
            (candidate, row_limit),
            timeout_seconds,
        )
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
            reranked = rerank_result(query, result)
            existing = scored_rows.get(key)
            if existing is None or reranked > existing.score:
                result.score = reranked
                scored_rows[key] = result
    return scored_rows


def fallback_keyword_candidates(text: str) -> list[str]:
    tokens = tokenize(text)
    keywords = keyword_candidates(text, max_terms=5)
    candidates: list[str] = []
    if len(tokens) >= 2:
        candidates.append(f'"{" ".join(tokens[: min(4, len(tokens))])}"')
    for token in keywords[:3]:
        candidates.append(token)
        candidates.append(f"{token}*")
    if len(tokens) >= 2:
        candidates.append(" OR ".join(tokens[: min(4, len(tokens))]))
    return [candidate for index, candidate in enumerate(candidates) if candidate and candidate not in candidates[:index]]


def fallback_fetch_results(query: str, conn: sqlite3.Connection, limit: int = 8) -> list[SearchResult]:
    candidates = fallback_keyword_candidates(query)
    if not candidates:
        return []

    scored_rows = collect_scored_rows(
        conn,
        query,
        candidates,
        timeout_seconds=PRIMARY_QUERY_TIMEOUT_SECONDS / 2,
        row_limit=40,
    )
    return sorted(scored_rows.values(), key=lambda item: item.score, reverse=True)[:limit]


def is_short_query(text: str) -> bool:
    tokens = tokenize(text)
    return 0 < len(tokens) <= SHORT_QUERY_TOKEN_LIMIT


def short_query_fetch_results(query: str, conn: sqlite3.Connection, limit: int = 8) -> list[SearchResult]:
    tokens = tokenize(query)
    if not tokens:
        return []

    phrase = " ".join(tokens)
    sql = """
        SELECT
            passages.title,
            passages.author,
            passages.year,
            passages.source_url,
            passages.text,
            0.0 AS score
        FROM passages
        WHERE lower(passages.text) LIKE ?
        LIMIT 80
    """
    like_rows = execute_with_timeout(
        conn,
        sql,
        (token_like_pattern(phrase),),
        PRIMARY_QUERY_TIMEOUT_SECONDS / 2,
    )

    scored_rows: dict[tuple[str, str, str, str, str], SearchResult] = {}
    for row in like_rows:
        result = SearchResult(
            title=row["title"],
            author=row["author"],
            year=row["year"],
            source_url=row["source_url"],
            text=row["text"],
            score=0.0,
        )
        key = (result.title, result.author, result.year, result.source_url, result.text)
        exact_bonus = 4.0 if phrase in normalized_text(result.text) else 0.0
        reranked = rerank_result(query, result) + exact_bonus + (0.8 if not is_secondary_source(result) else 0.0)
        existing = scored_rows.get(key)
        if existing is None or reranked > existing.score:
            result.score = reranked
            scored_rows[key] = result

    if not scored_rows:
        fts_candidates = [f'"{phrase}"', " AND ".join(tokens)]
        scored_rows = collect_scored_rows(
            conn,
            query,
            [candidate for candidate in fts_candidates if candidate],
            timeout_seconds=PRIMARY_QUERY_TIMEOUT_SECONDS / 2,
            row_limit=40,
        )

    ranked = sorted(scored_rows.values(), key=lambda item: item.score, reverse=True)
    primary_ranked = [result for result in ranked if not is_secondary_source(result)]
    if primary_ranked:
        return primary_ranked[:limit]
    return ranked[:limit]


def long_query_fetch_results(query: str, conn: sqlite3.Connection, limit: int = 8) -> list[SearchResult]:
    phrases = recovery_phrase_queries(query)
    if not phrases:
        return []

    source_best: dict[str, SearchResult] = {}
    source_totals: dict[str, float] = {}
    source_hits: dict[str, int] = {}

    def accumulate(candidate: SearchResult, bonus: float) -> None:
        source_key = candidate.source_url or f"{candidate.title}|{candidate.author}"
        score = rerank_result(query, candidate) + bonus
        if not is_secondary_source(candidate):
            score += 1.0
        source_totals[source_key] = source_totals.get(source_key, 0.0) + score
        source_hits[source_key] = source_hits.get(source_key, 0) + 1
        existing = source_best.get(source_key)
        if existing is None or score > existing.score:
            candidate.score = score
            source_best[source_key] = candidate

    for phrase in phrases:
        fts_rows = execute_with_timeout(
            conn,
            """
            SELECT
                passages.title,
                passages.author,
                passages.year,
                passages.source_url,
                passages.text,
                0.0 AS score
            FROM passages_fts
            JOIN passages ON passages_fts.rowid = passages.id
            WHERE passages_fts MATCH ?
            LIMIT 40
            """,
            (f'"{phrase}"',),
            PRIMARY_QUERY_TIMEOUT_SECONDS / 3,
        )
        for row in fts_rows:
            candidate = SearchResult(
                title=row["title"],
                author=row["author"],
                year=row["year"],
                source_url=row["source_url"],
                text=row["text"],
                score=row["score"],
            )
            accumulate(candidate, 2.8 + exact_phrase_score(phrase, candidate.text[:2200]))

    if len(phrases) >= 2:
        conjunction = f'"{phrases[0]}" AND "{phrases[-1]}"'
        fts_rows = execute_with_timeout(
            conn,
            """
            SELECT
                passages.title,
                passages.author,
                passages.year,
                passages.source_url,
                passages.text,
                0.0 AS score
            FROM passages_fts
            JOIN passages ON passages_fts.rowid = passages.id
            WHERE passages_fts MATCH ?
            LIMIT 40
            """,
            (conjunction,),
            PRIMARY_QUERY_TIMEOUT_SECONDS / 3,
        )
        for row in fts_rows:
            candidate = SearchResult(
                title=row["title"],
                author=row["author"],
                year=row["year"],
                source_url=row["source_url"],
                text=row["text"],
                score=row["score"],
            )
            accumulate(candidate, 5.0)

    scored_rows: dict[tuple[str, str, str, str, str], SearchResult] = {}
    for source_key, candidate in source_best.items():
        candidate.score = source_totals[source_key] + min(source_hits[source_key], 3) * 1.6
        key = (candidate.title, candidate.author, candidate.year, candidate.source_url, candidate.text)
        existing = scored_rows.get(key)
        if existing is None or candidate.score > existing.score:
            scored_rows[key] = candidate

    if not scored_rows:
        for phrase in phrases:
            like_rows = execute_with_timeout(
                conn,
                """
                SELECT
                    passages.title,
                    passages.author,
                    passages.year,
                    passages.source_url,
                    passages.text,
                    0.0 AS score
                FROM passages
                WHERE lower(passages.text) LIKE ?
                LIMIT 120
                """,
                (token_like_pattern(phrase),),
                PRIMARY_QUERY_TIMEOUT_SECONDS / 3,
            )
            for row in like_rows:
                result = SearchResult(
                    title=row["title"],
                    author=row["author"],
                    year=row["year"],
                    source_url=row["source_url"],
                    text=row["text"],
                    score=0.0,
                )
                key = (result.title, result.author, result.year, result.source_url, result.text)
                exact_bonus = 4.5 if normalized_text(phrase) in normalized_text(result.text) else 0.0
                reranked = (
                    rerank_result(query, result)
                    + exact_bonus
                    + (1.2 if not is_secondary_source(result) else 0.0)
                )
                existing = scored_rows.get(key)
                if existing is None or reranked > existing.score:
                    result.score = reranked
                    scored_rows[key] = result

    ranked = sorted(scored_rows.values(), key=lambda item: item.score, reverse=True)
    primary_ranked = [result for result in ranked if not is_secondary_source(result)]
    if primary_ranked:
        return primary_ranked[:limit]
    return ranked[:limit]


@lru_cache(maxsize=1024)
def fetch_results_cached(query: str, limit: int, db_path_str: str) -> tuple[tuple[str, str, str, str, str, float], ...]:
    db_path = Path(db_path_str)
    if not db_path.exists():
        return ()

    candidates = query_candidates(query)
    if not candidates:
        return ()

    started = time.monotonic()
    conn = connect_db(db_path)
    try:
        if is_short_query(query):
            short_results = short_query_fetch_results(query, conn, limit=limit)
            if short_results:
                return tuple(
                    (result.title, result.author, result.year, result.source_url, result.text, result.score)
                    for result in short_results[:limit]
                )

        token_count = len(tokenize(query))
        if token_count >= LONG_QUERY_TOKEN_THRESHOLD:
            long_results = long_query_fetch_results(query, conn, limit=limit)
            if long_results:
                return tuple(
                    (result.title, result.author, result.year, result.source_url, result.text, result.score)
                    for result in long_results[:limit]
                )

        row_limit = 8 if token_count >= LONG_QUERY_TOKEN_THRESHOLD else 12
        primary_timeout = 3.0 if token_count >= LONG_QUERY_TOKEN_THRESHOLD else PRIMARY_QUERY_TIMEOUT_SECONDS
        scored_rows = collect_scored_rows(
            conn,
            query,
            candidates[:3],
            timeout_seconds=primary_timeout,
            row_limit=row_limit,
        )
        if not scored_rows or len(tokenize(query)) <= 3 or (time.monotonic() - started) >= FALLBACK_AFTER_SECONDS:
            fallback_results = fallback_fetch_results(query, conn, limit=limit)
            for result in fallback_results:
                key = (result.title, result.author, result.year, result.source_url, result.text)
                existing = scored_rows.get(key)
                if existing is None or result.score > existing.score:
                    scored_rows[key] = result
    finally:
        conn.close()

    return tuple(
        (result.title, result.author, result.year, result.source_url, result.text, result.score)
        for result in sorted(scored_rows.values(), key=lambda item: item.score, reverse=True)[:limit]
    )


def fetch_results(query: str, limit: int = 8, db_path: Path | None = None) -> list[SearchResult]:
    db_path = resolve_db_path(db_path)
    return thaw_result_rows(fetch_results_cached(query, limit, str(db_path)))


def sentence_score(segment: str, candidate: str) -> float:
    segment_tokens = set(tokenize(segment))
    candidate_tokens = set(tokenize(candidate))
    if not segment_tokens or not candidate_tokens:
        return 0.0

    overlap = len(segment_tokens & candidate_tokens)
    density = overlap / max(len(candidate_tokens), 1)
    coverage = overlap / max(len(segment_tokens), 1)
    return coverage * 2 + density


def exact_phrase_score(segment: str, candidate: str) -> float:
    normalized_segment = normalized_text(segment)
    normalized_candidate = normalized_text(candidate)
    if not normalized_segment or not normalized_candidate:
        return 0.0
    if normalized_segment in normalized_candidate:
        return 3.0

    phrases = [phrase.strip('"') for phrase in phrase_candidates(segment)]
    for phrase in phrases:
        if phrase and phrase in normalized_candidate:
            return 1.6
    return 0.0


def sequence_score(segment: str, candidate: str) -> float:
    normalized_segment = normalized_text(segment)
    normalized_candidate = normalized_text(candidate)
    if not normalized_segment or not normalized_candidate:
        return 0.0
    if len(normalized_segment.split()) >= LONG_QUERY_TOKEN_THRESHOLD:
        return 0.0
    return SequenceMatcher(None, normalized_segment, normalized_candidate).ratio()


def best_quote(segment: str, result: SearchResult) -> str:
    sentences = split_sentences(result.text)
    if not sentences:
        return result.text.strip()

    segment_tokens = tokenize(segment)
    short_query = len(segment_tokens) <= 6
    best_window_score = -1.0
    best_window_text = ""
    max_window = 1 if short_query else min(3, len(sentences))
    for window in range(1, max_window + 1):
        for start in range(0, len(sentences) - window + 1):
            passage = " ".join(sentence.strip() for sentence in sentences[start : start + window]).strip()
            score = (
                sentence_score(segment, passage)
                + exact_phrase_score(segment, passage)
                + sequence_score(segment, passage)
            )
            if short_query:
                normalized_passage = normalized_text(passage)
                normalized_segment = normalized_text(segment)
                if normalized_segment and normalized_segment in normalized_passage:
                    score += 3.5
                score -= max(0, len(tokenize(passage)) - max(len(segment_tokens) * 3, 14)) * 0.06
            if score > best_window_score:
                best_window_score = score
                best_window_text = passage

    if best_window_score <= 0:
        fallback = sentences[0].strip()
        return clean_quote_text(fallback)
    return clean_quote_text(best_window_text)


def clean_quote_text(text: str) -> str:
    cleaned = LEADING_ARTIFACT_RE.sub("", text).strip()
    cleaned = LEADING_HEADING_RE.sub("", cleaned).strip()
    cleaned = cleaned.replace("“ ", "“").replace('" ', '"')
    return cleaned


def extract_inner_quote(text: str) -> str | None:
    match = INNER_QUOTE_RE.search(text)
    if not match:
        return None
    return match.group(1).strip()


def extract_refinement_query(segment: str, quote: str, result: SearchResult) -> str | None:
    inner = extract_inner_quote(quote)
    if inner:
        return inner
    if is_secondary_source(result):
        normalized_segment = normalized_text(segment)
        normalized_quote = normalized_text(quote)
        if normalized_segment and normalized_quote and normalized_segment in normalized_quote:
            return segment
        quoted_phrases = re.findall(r"[\"“]([^\"”]{3,})[\"”]", quote)
        if quoted_phrases:
            return max(quoted_phrases, key=len).strip()
        return segment
    return None


def refine_primary_source(segment: str, quote: str, result: SearchResult, db_path: Path | None) -> tuple[str, SearchResult]:
    db_path = resolve_db_path(db_path)
    refinement_query = extract_refinement_query(segment, quote, result)
    if not refinement_query:
        return quote, result

    candidates = fetch_results(refinement_query, limit=12, db_path=db_path)
    if not candidates:
        return quote, result

    preferred_pool = [candidate for candidate in candidates if not is_secondary_source(candidate)]
    if not preferred_pool:
        preferred_pool = candidates

    preferred = sorted(
        preferred_pool,
        key=lambda candidate: (
            (
                sentence_score(refinement_query, candidate.text[:2200])
                + exact_phrase_score(refinement_query, candidate.text[:2200])
                + source_preference(candidate, refinement_query)
            ),
            -len(candidate.title),
        ),
        reverse=True,
    )[0]
    refined_quote = best_quote(refinement_query, preferred)
    return refined_quote, preferred


def recover_primary_match(segment: str, result: SearchResult, db_path: Path | None) -> SearchResult:
    if not is_secondary_source(result):
        return result

    best_candidate = result
    best_score = rerank_result(segment, result)
    source_best: dict[str, SearchResult] = {}
    source_totals: dict[str, float] = {}
    source_hits: dict[str, int] = {}
    db_path = resolve_db_path(db_path)
    conn = connect_db(db_path)
    try:
        for phrase in recovery_phrase_queries(segment):
            like_rows = execute_with_timeout(
                conn,
                """
                SELECT
                    passages.title,
                    passages.author,
                    passages.year,
                    passages.source_url,
                    passages.text,
                    0.0 AS score
                FROM passages
                WHERE lower(passages.text) LIKE ?
                LIMIT 80
                """,
                (token_like_pattern(phrase),),
                PRIMARY_QUERY_TIMEOUT_SECONDS / 3,
            )
            for row in like_rows:
                candidate = SearchResult(
                    title=row["title"],
                    author=row["author"],
                    year=row["year"],
                    source_url=row["source_url"],
                    text=row["text"],
                    score=0.0,
                )
                if is_secondary_source(candidate):
                    continue
                score = (
                    rerank_result(segment, candidate)
                    + exact_phrase_score(segment, candidate.text[:2200])
                    + 1.5
                )
                source_key = candidate.source_url or f"{candidate.title}|{candidate.author}"
                source_totals[source_key] = source_totals.get(source_key, 0.0) + score
                source_hits[source_key] = source_hits.get(source_key, 0) + 1
                existing_for_source = source_best.get(source_key)
                if existing_for_source is None or score > existing_for_source.score:
                    candidate.score = score
                    source_best[source_key] = candidate
                if score > best_score:
                    candidate.score = score
                    best_candidate = candidate
                    best_score = score

        for phrase in recovery_phrase_queries(segment):
            candidates = fetch_results(phrase, limit=8, db_path=db_path)
            for candidate in candidates:
                if is_secondary_source(candidate):
                    continue
                score = (
                    rerank_result(segment, candidate)
                    + exact_phrase_score(segment, candidate.text[:2200])
                    + 1.0
                )
                source_key = candidate.source_url or f"{candidate.title}|{candidate.author}"
                source_totals[source_key] = source_totals.get(source_key, 0.0) + score
                source_hits[source_key] = source_hits.get(source_key, 0) + 1
                existing_for_source = source_best.get(source_key)
                if existing_for_source is None or score > existing_for_source.score:
                    candidate.score = score
                    source_best[source_key] = candidate
                if score > best_score:
                    candidate.score = score
                    best_candidate = candidate
                    best_score = score
    finally:
        conn.close()

    if source_totals:
        best_source_key = max(
            source_totals,
            key=lambda key: (
                source_totals[key] + min(source_hits.get(key, 0), 3) * 1.4,
                source_hits.get(key, 0),
                -len(source_best[key].title),
            ),
        )
        source_candidate = source_best[best_source_key]
        source_candidate.score = source_totals[best_source_key] + min(source_hits.get(best_source_key, 0), 3) * 1.4
        if source_candidate.score > best_score:
            best_candidate = source_candidate

    return best_candidate


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

        result = recover_primary_match(segment, result, db_path)
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
