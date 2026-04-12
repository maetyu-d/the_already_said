#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from engine import compose_quotation_text, fetch_results, stats


if getattr(sys, "frozen", False):
    ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
else:
    ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"


class GutenbergHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def _send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/search":
            query = parse_qs(parsed.query).get("q", [""])[0]
            results = fetch_results(query)
            self._send_json(
                {
                    "results": [
                        {
                            "title": result.title,
                            "author": result.author,
                            "year": result.year,
                            "sourceUrl": result.source_url,
                            "text": result.text,
                        }
                        for result in results
                    ]
                }
            )
            return

        if parsed.path == "/api/stats":
            self._send_json(stats())
            return

        super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/compose":
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self._send_json({"error": "Invalid JSON"}, HTTPStatus.BAD_REQUEST)
            return

        text = payload.get("text", "")
        style = payload.get("style", "harvard")
        self._send_json(compose_quotation_text(text, style))

    def log_message(self, format: str, *args) -> None:
        sys.stdout.write(f"{self.address_string()} - {format % args}\n")


def main() -> None:
    server = make_server()
    print("Serving at http://127.0.0.1:8000")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server.")


def make_server(host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), GutenbergHandler)


if __name__ == "__main__":
    main()
