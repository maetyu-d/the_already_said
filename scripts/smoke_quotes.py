#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import compose_quotation_text


CASES = [
    ("A single man in possession of a good fortune must be in want of a wife.", "Pride and Prejudice"),
    ("Call me Ishmael.", "Moby-Dick; or, The Whale"),
    ("Marley was dead: to begin with.", "A Christmas Carol in Prose; Being a Ghost Story of Christmas"),
    ("It was the best of times, it was the worst of times.", "A Tale of Two Cities"),
    ("All happy families are alike; each unhappy family is unhappy in its own way.", "Anna Karenina"),
    ("It was a bright cold day in April, and the clocks were striking thirteen.", None),
    ("I am an invisible man.", None),
    ("Happy families are all alike; every unhappy family is unhappy in its own way.", "Anna Karenina"),
]


def main() -> None:
    report = []
    for query, expected_title in CASES:
        started = time.perf_counter()
        payload = compose_quotation_text(query, "harvard")
        elapsed = time.perf_counter() - started
        match = payload["matches"][0] if payload["matches"] else {}
        report.append(
            {
                "query": query,
                "expected_title": expected_title,
                "title": match.get("title"),
                "author": match.get("author"),
                "quote": match.get("quote"),
                "sourceUrl": match.get("sourceUrl"),
                "seconds": round(elapsed, 3),
                "matched_expected": (match.get("title") == expected_title) if expected_title else None,
                "found_match": bool(match),
                "composite": bool(match.get("composite")),
                "components": len(match.get("components", [])) if match else 0,
            }
        )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
