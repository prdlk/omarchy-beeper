"""All HTTP to the local Beeper Desktop API.

Everything here is bound to http://localhost:23373. The token is read from a
0600 file and only ever leaves this process inside an Authorization header:
never in argv, never on stderr, never in a payload.

Endpoints used (https://developers.beeper.com/desktop-api-reference/):

  GET  /v1/info                     server discovery, no auth
  GET  /v1/accounts                 connected chat accounts
  GET  /v1/chats/search             unread chats, cursor paginated
  GET  /v1/messages/search          last message for the row snippet
  GET  /v1/chats/{chatID}/messages  snippet fallback for unindexed chats
  POST /v1/chats/{chatID}/read      mark a chat read
  POST /v1/focus                    focus Beeper Desktop, optionally a chat
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from common import (
    API_BASE,
    FETCH_CAP,
    MAX_HTTP_ERROR,
    encode_id,
    one_line,
    read_http_body,
)

REQUEST_TIMEOUT = 30
SEARCH_PAGE = 200  # /v1/chats/search caps limit at 200
SNIPPET_SEARCH_LIMIT = 5  # a few, so a deleted newest message is not fatal
SNIPPET_WORKERS = 8

# Message kinds with no body text. Shown as a short placeholder instead.
MEDIA_LABELS = {
    "IMAGE": "[photo]",
    "VIDEO": "[video]",
    "VOICE": "[voice message]",
    "AUDIO": "[audio]",
    "FILE": "[file]",
    "STICKER": "[sticker]",
    "LOCATION": "[location]",
}


class ApiError(Exception):
    """An API call that came back, but not with a usable answer."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class UnreachableError(Exception):
    """Beeper Desktop is not listening on the local port."""


class AuthError(Exception):
    """The token is missing, expired, or was revoked."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect could point off localhost, so treat any 3xx as an error."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _url(path: str, params: dict | None = None) -> str:
    if not path.startswith("/v1/"):
        raise ApiError(f"refusing to call {path}")
    url = API_BASE + path
    if params:
        pairs: list[tuple[str, str]] = []
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, bool):
                pairs.append((key, "true" if value else "false"))
            elif isinstance(value, (list, tuple)):
                pairs.extend((key, str(item)) for item in value)
            else:
                pairs.append((key, str(value)))
        if pairs:
            url += "?" + urllib.parse.urlencode(pairs)
    if not url.startswith(API_BASE + "/"):
        raise ApiError("refusing to leave the local API")
    return url


def request(
    token: str,
    method: str,
    path: str,
    params: dict | None = None,
    body: dict | None = None,
    timeout: int = REQUEST_TIMEOUT,
) -> object:
    """One call to the local API. Returns the decoded JSON body, or None."""
    payload = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(_url(path, params), data=payload, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    try:
        with _OPENER.open(req, timeout=timeout) as fp:
            raw = read_http_body(fp)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = read_http_body(exc, MAX_HTTP_ERROR).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - the status matters, the body does not
            detail = ""
        finally:
            exc.close()
        if exc.code in (401, 403):
            raise AuthError("token missing or invalid; run: omarchy-beeper auth") from exc
        raise ApiError(_http_message(exc.code, detail), exc.code) from exc
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise ApiError("the local API timed out") from exc
        if isinstance(reason, OSError):
            # Only localhost is ever dialled, so any socket error means the
            # port is not being served: Beeper Desktop is down or restarting.
            raise UnreachableError("Beeper Desktop is not running") from exc
        raise ApiError(one_line(str(reason)) or "the local API failed") from exc
    except (socket.timeout, TimeoutError) as exc:
        raise ApiError("the local API timed out") from exc
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiError("the local API returned invalid JSON") from exc


def _http_message(status: int, detail: str) -> str:
    text = ""
    try:
        data = json.loads(detail)
        if isinstance(data, dict):
            text = str(data.get("message") or data.get("error") or "")
    except (json.JSONDecodeError, ValueError):
        text = ""
    text = one_line(text, 120)
    return f"Beeper API error {status}: {text}" if text else f"Beeper API error {status}"


def server_info() -> dict:
    """GET /v1/info needs no token, so it separates 'down' from 'bad token'."""
    data = request("", "GET", "/v1/info", timeout=5)
    return data if isinstance(data, dict) else {}


def probe(token: str) -> None:
    """Cheapest authenticated call, used to verify a token during `auth`."""
    request(token, "GET", "/v1/accounts", timeout=10)


def accounts(token: str) -> list[dict]:
    data = request(token, "GET", "/v1/accounts")
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def account_label(account: dict) -> str:
    return one_line(str(account.get("network") or account.get("accountID") or ""), 40)


def in_pile(chat: dict) -> bool:
    """Is this chat one the user still has to look at?

    Muted, archived, and low-priority chats are out. The decision is made
    here, on the flags the chat object carries, and not with the search
    endpoint's `inbox`/`includeMuted` filters: measured against Beeper
    Desktop 4.3.73, `includeMuted=false` returns muted chats anyway, and
    `inbox=primary` combined with `unreadOnly=true` hides ordinary unread
    chats (75 of 80 on the machine this was tested on). The per-chat flags
    are exact.
    """
    if chat.get("isMuted") or chat.get("isArchived") or chat.get("isLowPriority"):
        return False
    try:
        unread = int(chat.get("unreadCount") or 0)
    except (TypeError, ValueError):
        unread = 0
    return unread > 0 or bool(chat.get("isMarkedUnread"))


def unread_chats(token: str, cap: int = FETCH_CAP, budget: float = 0.0) -> tuple[list[dict], bool]:
    """Unread chats, newest first, muted/archived/low-priority removed.

    Returns (chats, truncated) where truncated means the walk hit `cap` or
    `budget` before the API ran out of pages.
    """
    out: list[dict] = []
    seen: set[str] = set()
    cursor = ""
    truncated = False
    deadline = (time.monotonic() + budget) if budget > 0 else 0.0
    while True:
        params: dict = {"unreadOnly": True, "limit": SEARCH_PAGE}
        if cursor:
            params["cursor"] = cursor
            params["direction"] = "before"
        data = request(token, "GET", "/v1/chats/search", params)
        if not isinstance(data, dict):
            raise ApiError("the local API returned invalid JSON")
        items = data.get("items")
        if not isinstance(items, list):
            items = []
        for chat in items:
            if not isinstance(chat, dict):
                continue
            chat_id = str(chat.get("id") or "")
            if not chat_id or chat_id in seen:
                continue
            seen.add(chat_id)
            if in_pile(chat):
                out.append(chat)
        if len(out) >= cap:
            truncated = bool(data.get("hasMore")) or len(out) > cap
            del out[cap:]
            break
        cursor = str(data.get("oldestCursor") or "")
        if not data.get("hasMore") or not cursor or not items:
            break
        if deadline and time.monotonic() >= deadline:
            truncated = True
            break
    out.sort(key=lambda chat: (_ts(chat.get("lastActivity")), str(chat.get("id"))), reverse=True)
    return out, truncated


def _newest(data: object) -> dict:
    """Newest message in a response that is worth showing as a snippet."""
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return {}
    best: dict = {}
    best_key = (0.0, "")
    for msg in items:
        if not isinstance(msg, dict):
            continue
        if msg.get("isDeleted") or msg.get("isHidden") or msg.get("type") == "REACTION":
            continue
        key = (_ts(msg.get("timestamp")), str(msg.get("sortKey") or ""))
        if not best or key > best_key:
            best, best_key = msg, key
    return best


def last_message(token: str, chat_id: str) -> dict:
    """Newest displayable message in a chat, for the row snippet.

    The search index answers in about a millisecond; listing a chat's
    messages takes ~145 ms because it returns a whole page. So ask the index
    first and only fall back for the few chats it has not indexed yet
    (measured: 2 of 40 on a warm install, both recovered by the fallback).
    """
    indexed = _newest(
        request(
            token,
            "GET",
            "/v1/messages/search",
            {
                "chatIDs": [chat_id],
                "limit": SNIPPET_SEARCH_LIMIT,
                # Neither filter should ever hide the snippet of a chat that
                # already earned its place in the pile.
                "excludeLowPriority": False,
                "includeMuted": True,
            },
        )
    )
    if indexed:
        return indexed
    path = f"/v1/chats/{urllib.parse.quote(chat_id, safe='')}/messages"
    return _newest(request(token, "GET", path))


def message_preview(msg: dict) -> str:
    text = one_line(str(msg.get("text") or ""))
    if text:
        return text
    kind = str(msg.get("type") or "")
    if kind in MEDIA_LABELS:
        return MEDIA_LABELS[kind]
    attachments = msg.get("attachments")
    if isinstance(attachments, list) and attachments:
        return "[attachment]"
    return ""


def mark_read(token: str, chat_id: str, timeout: int = REQUEST_TIMEOUT) -> None:
    request(
        token,
        "POST",
        f"/v1/chats/{urllib.parse.quote(chat_id, safe='')}/read",
        body={},
        timeout=timeout,
    )


def focus(token: str, chat_id: str = "") -> None:
    body: dict = {}
    if chat_id:
        body["chatID"] = chat_id
    request(token, "POST", "/v1/focus", body=body)


def chat_row(chat: dict, msg: dict | None = None) -> dict:
    """One panel row. Every display string goes through one_line()."""
    msg = msg or {}
    title = one_line(str(chat.get("title") or ""))
    network = one_line(str(chat.get("network") or ""), 40)
    if not title:
        title = network or "(no title)"
    sender = one_line(str(msg.get("senderName") or ""), 60)
    if msg.get("isSender"):
        sender = "You"
    ts = _ts(msg.get("timestamp")) or _ts(chat.get("lastActivity"))
    return {
        "id": encode_id(str(chat.get("id") or "")),
        "threadId": str(chat.get("id") or ""),
        "subject": title,
        "from": sender,
        "snippet": message_preview(msg),
        "ts": int(ts),
        "labels": [network] if network else [],
        "url": "",
    }


def _ts(value: object) -> float:
    """ISO 8601 (or unix seconds) to unix seconds. Unparsable is 0."""
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return 0.0
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()
