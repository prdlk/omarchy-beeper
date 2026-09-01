"""Shared helpers for mocked contract tests. Never talks to Beeper."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
from contextlib import contextmanager, redirect_stdout
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"

if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))


def capture_json(fn, *args, **kwargs) -> dict:
    buf = StringIO()
    with redirect_stdout(buf):
        try:
            fn(*args, **kwargs)
        except SystemExit:
            pass
    text = buf.getvalue().strip()
    if not text:
        raise AssertionError("expected JSON on stdout")
    return json.loads(text.splitlines()[-1])


class FakeResponse:
    """Enough of an http.client.HTTPResponse for read_http_body()."""

    def __init__(self, payload: object, status: int = 200, content_length: object | None = None):
        if isinstance(payload, (bytes, bytearray)):
            raw = bytes(payload)
        else:
            raw = json.dumps(payload).encode("utf-8")
        self._buf = BytesIO(raw)
        self.status = status
        declared = len(raw) if content_length is None else content_length
        self.headers = {} if declared is False else {"Content-Length": str(declared)}

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class Call:
    def __init__(self, method: str, url: str, body: object) -> None:
        parsed = urllib.parse.urlsplit(url)
        self.method = method
        self.url = url
        self.host = parsed.netloc
        self.path = parsed.path
        self.query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        self.body = body

    def one(self, key: str, default: str = "") -> str:
        values = self.query.get(key) or []
        return values[0] if values else default

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Call {self.method} {self.path} {self.query}>"


class FakeHttp:
    """Route (METHOD, path) to a callable or a fixed response."""

    def __init__(self, routes: dict[tuple[str, str], object] | None = None) -> None:
        self.routes = dict(routes or {})
        self.calls: list[Call] = []

    def route(self, method: str, path: str, handler: object) -> None:
        self.routes[(method, path)] = handler

    def open(self, req, timeout: int | None = None):  # noqa: D102 - urlopen shape
        body = None
        if req.data:
            body = json.loads(req.data.decode("utf-8"))
        call = Call(req.get_method(), req.full_url, body)
        self.calls.append(call)
        handler = self.routes.get((call.method, call.path))
        if handler is None:
            raise urllib.error.HTTPError(call.url, 404, "Not Found", {}, BytesIO(b"{}"))
        result = handler(call) if callable(handler) else handler
        if isinstance(result, Exception):
            raise result
        if isinstance(result, FakeResponse):
            return result
        return FakeResponse(result)

    def paths(self, method: str = "") -> list[str]:
        return [c.path for c in self.calls if not method or c.method == method]


@contextmanager
def fake_api(http: FakeHttp):
    import beeper

    with patch.object(beeper, "_OPENER", http):
        yield http


def http_error(status: int, message: str = "nope", url: str = "http://localhost:23373/v1/x"):
    body = json.dumps({"message": message, "code": "error"}).encode("utf-8")
    return urllib.error.HTTPError(url, status, message, {"Content-Length": str(len(body))}, BytesIO(body))


def url_error(reason: Exception | None = None):
    return urllib.error.URLError(reason or ConnectionRefusedError(111, "Connection refused"))


def chat(
    chat_id: str,
    *,
    title: str = "Ada Lovelace",
    network: str = "WhatsApp",
    account: str = "local-whatsapp_1",
    last_activity: str = "2026-02-11T10:00:00.000Z",
    unread: int = 2,
) -> dict:
    return {
        "id": chat_id,
        "localChatID": "1",
        "accountID": account,
        "network": network,
        "title": title,
        "type": "single",
        "participants": {"items": [], "hasMore": False, "total": 1},
        "lastActivity": last_activity,
        "unreadCount": unread,
        "isArchived": False,
        "isMuted": False,
        "isPinned": False,
    }


def message(
    chat_id: str,
    *,
    text: str = "are you coming tonight?",
    sender: str = "Ada",
    timestamp: str = "2026-02-11T10:00:00.000Z",
    kind: str = "TEXT",
    sort_key: str = "100",
) -> dict:
    return {
        "id": "m1",
        "chatID": chat_id,
        "accountID": "local-whatsapp_1",
        "senderID": "@ada:example",
        "senderName": sender,
        "timestamp": timestamp,
        "sortKey": sort_key,
        "type": kind,
        "text": text,
    }


def account(account_id: str, network: str) -> dict:
    return {
        "accountID": account_id,
        "loginID": account_id,
        "bridge": {"id": network.lower(), "type": network.lower(), "provider": "local"},
        "network": network,
        "user": {"id": "@me:example", "isSelf": True},
        "status": "connected",
    }


def search_page(chats: list[dict], has_more: bool = False, cursor: str | None = None) -> dict:
    return {
        "items": chats,
        "hasMore": has_more,
        "oldestCursor": cursor,
        "newestCursor": None,
    }
