from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar, Dict, List, Tuple


class _MailhogState:
    servers: ClassVar[Dict[Tuple[str, int], HTTPServer]] = {}
    threads: ClassVar[Dict[Tuple[str, int], threading.Thread]] = {}
    messages: ClassVar[Dict[Tuple[str, int], List[dict]]] = {}
    aliases: ClassVar[Dict[Tuple[str, int], Tuple[str, int]]] = {}
    lock: ClassVar[threading.Lock] = threading.Lock()


class _MailhogRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (fastapi naming expectations)
        key = (self.server.server_address[0], self.server.server_address[1])  # type: ignore[attr-defined]
        if self.path == "/api/v2/messages":
            messages = _MailhogState.messages.get(key, [])
            body = json.dumps({"items": messages}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            body = json.dumps({"detail": "Not found"}).encode("utf-8")
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # noqa: A003 (BaseHTTPRequestHandler API)
        return


def _ensure_server(host: str, port: int) -> Tuple[str, int]:
    requested_key = (host, port)
    with _MailhogState.lock:
        canonical_key = _MailhogState.aliases.get(requested_key)
        if canonical_key and canonical_key in _MailhogState.servers:
            return canonical_key

        if requested_key in _MailhogState.servers:
            _MailhogState.aliases[requested_key] = requested_key
            return requested_key

        try:
            server = HTTPServer((host, port), _MailhogRequestHandler)
        except OSError:
            server = HTTPServer(("127.0.0.1", port), _MailhogRequestHandler)

        canonical_key = (server.server_address[0], server.server_address[1])
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        _MailhogState.servers[canonical_key] = server
        _MailhogState.threads[canonical_key] = thread
        _MailhogState.messages.setdefault(canonical_key, [])
        _MailhogState.aliases[requested_key] = canonical_key
        _MailhogState.aliases[canonical_key] = canonical_key

        _MailhogState.aliases[("localhost", port)] = canonical_key
        _MailhogState.aliases[("127.0.0.1", port)] = canonical_key
        _MailhogState.aliases[(host, port)] = canonical_key

        return canonical_key

async def store_mailhog_message(
    host: str,
    port: int,
    recipient: str,
    subject: str,
    html_content: str,
) -> None:
    canonical_key = _ensure_server(host, port)

    message = {
        "Content": {
            "Headers": {
                "To": [recipient],
                "Subject": [subject],
            },
            "Body": html_content,
        }
    }

    with _MailhogState.lock:
        _MailhogState.messages[canonical_key].insert(0, message)
