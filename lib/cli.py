#!/usr/bin/env python3
"""CLI entry for omarchy-beeper.

Every command prints exactly one JSON object and exits 0, so the bar widget
never has to deal with empty stdout or a stack trace.
"""

from __future__ import annotations

import getpass
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

LIB = Path(__file__).resolve().parent
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import beeper
from common import (
    FETCH_CAP,
    TOKEN_FILE,
    die,
    decode_id,
    emit,
    load_token,
    max_chats,
    save_token,
)

# The panel is on a refresh timer, so a slow list must still return.
LIST_BUDGET = 40.0
READ_ALL_BUDGET = 100.0
READ_ALL_CAP = 1000
READ_ALL_WORKERS = 6

HELP = """\
omarchy-beeper list [--limit N] [--page OFFSET]
omarchy-beeper read <id>
omarchy-beeper read-all
omarchy-beeper open [<id>]
omarchy-beeper auth

The bar widget calls list, read, read-all, and open. The token is set up
once in a terminal:

  omarchy-beeper auth

--limit is the panel page size (1-50); the CLI also honours
OMARCHY_BEEPER_MAX. One row is one unread chat. Muted, archived, and
low-priority chats are not part of the pile.
"""


def _fail(exc: Exception) -> None:
    """Turn an API exception into the single JSON error line."""
    if isinstance(exc, beeper.UnreachableError):
        die("Beeper Desktop is not running")
    if isinstance(exc, beeper.AuthError):
        die("token missing or invalid; run: omarchy-beeper auth")
    die(str(exc) or "the local Beeper API failed")


def _token() -> str:
    token = load_token()
    if token:
        return token
    # No token yet: say which of the two setup steps is missing.
    try:
        beeper.server_info()
    except (beeper.UnreachableError, beeper.ApiError):
        die("Beeper Desktop is not running")
    die("token missing or invalid; run: omarchy-beeper auth")
    return ""  # unreachable, keeps type checkers quiet


def _inboxes(accounts: list[dict], chats: list[dict]) -> list[dict]:
    """Unread chats per connected account, in the order the API lists them."""
    counts: dict[str, int] = {}
    for chat in chats:
        acc_id = str(chat.get("accountID") or "")
        counts[acc_id] = counts.get(acc_id, 0) + 1
    out = []
    claimed = 0
    for account in accounts:
        acc_id = str(account.get("accountID") or "")
        unread = counts.get(acc_id, 0)
        claimed += unread
        out.append(
            {
                "account": beeper.account_label(account) or acc_id,
                "unread": unread,
                "searchUrl": "",
            }
        )
    leftover = len(chats) - claimed
    if leftover > 0:
        # A chat on an account /v1/accounts did not report; still count it.
        out.append({"account": "Other", "unread": leftover, "searchUrl": ""})
    return out


def _snippets(token: str, chats: list[dict], deadline: float) -> dict[str, dict]:
    """Last message per displayed chat. A failure just loses the snippet."""
    if not chats:
        return {}

    def work(chat: dict) -> tuple[str, dict]:
        chat_id = str(chat.get("id") or "")
        if time.monotonic() >= deadline:
            return chat_id, {}
        try:
            return chat_id, beeper.last_message(token, chat_id)
        except (beeper.ApiError, beeper.UnreachableError, beeper.AuthError, OSError, ValueError):
            return chat_id, {}

    workers = min(beeper.SNIPPET_WORKERS, len(chats))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(pool.map(work, chats))


def cmd_list(page_token: str) -> None:
    started = time.monotonic()
    token = _token()
    per = max_chats()
    start = 0
    if page_token:
        try:
            start = max(0, int(page_token))
        except ValueError:
            start = 0

    try:
        chats, truncated = beeper.unread_chats(token, cap=FETCH_CAP, budget=LIST_BUDGET * 0.5)
        accounts = beeper.accounts(token)
    except Exception as exc:  # noqa: BLE001 - mapped to the JSON error contract
        _fail(exc)
        return

    unread = len(chats)
    chunk = chats[start : start + per]
    deadline = started + LIST_BUDGET
    previews = _snippets(token, chunk, deadline)
    rows = [beeper.chat_row(chat, previews.get(str(chat.get("id") or ""))) for chat in chunk]

    # A page past the pile would always be empty, so do not offer one.
    next_start = start + per
    next_page = str(next_start) if next_start < unread else ""

    out = {
        "ok": True,
        "unread": unread,
        "accountCount": len(accounts),
        "nextPage": next_page,
        "thisPage": str(start),
        "inboxes": _inboxes(accounts, chats),
        "messages": rows,
    }
    if truncated:
        out["warning"] = f"showing the newest {FETCH_CAP} unread chats"
    emit(out)


def cmd_read(opaque: str) -> None:
    chat_id = decode_id(opaque)
    token = _token()
    try:
        beeper.mark_read(token, chat_id)
    except Exception as exc:  # noqa: BLE001 - mapped to the JSON error contract
        _fail(exc)
        return
    emit({"ok": True})


def cmd_read_all() -> None:
    started = time.monotonic()
    deadline = started + READ_ALL_BUDGET
    token = _token()
    try:
        # Snapshot first: marking changes what an unread query would return.
        chats, _ = beeper.unread_chats(token, cap=READ_ALL_CAP, budget=READ_ALL_BUDGET * 0.3)
    except Exception as exc:  # noqa: BLE001 - mapped to the JSON error contract
        _fail(exc)
        return
    ids = [str(chat.get("id") or "") for chat in chats]
    ids = [chat_id for chat_id in ids if chat_id]
    if not ids:
        emit({"ok": True, "marked": 0})
        return

    marked = 0
    errors: list[str] = []
    ran_out = False

    def work(chat_id: str) -> str:
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            return "budget"
        try:
            beeper.mark_read(token, chat_id, timeout=int(min(beeper.REQUEST_TIMEOUT, remaining)))
        except Exception as exc:  # noqa: BLE001 - one chat failing is not fatal
            return str(exc) or "could not mark as read"
        return ""

    with ThreadPoolExecutor(max_workers=READ_ALL_WORKERS) as pool:
        for result in pool.map(work, ids):
            if result == "":
                marked += 1
            elif result == "budget":
                ran_out = True
            elif len(errors) < 3:
                errors.append(result)

    if ran_out and not errors:
        errors.append(f"stopped after {int(READ_ALL_BUDGET)}s")
    if errors and marked == 0:
        emit({"ok": False, "marked": 0, "error": "; ".join(errors)})
        return
    if errors:
        emit({"ok": False, "marked": marked, "error": "; ".join(errors)})
        return
    emit({"ok": True, "marked": marked})


def cmd_open(opaque: str) -> None:
    chat_id = decode_id(opaque) if opaque else ""
    token = _token()
    try:
        beeper.focus(token, chat_id)
    except Exception as exc:  # noqa: BLE001 - mapped to the JSON error contract
        _fail(exc)
        return
    emit({"ok": True})


AUTH_HELP = """\
Create an access token in Beeper Desktop:

  Settings -> Integrations -> create a token (read access is enough)

Then paste it here. It is stored in
"""


def cmd_auth() -> None:
    # A token typed into a pipe would end up in a shell history or a log.
    if not (sys.stdin.isatty() and sys.stderr.isatty()):
        sys.stderr.write("omarchy-beeper auth needs a terminal; see docs/SETUP.md\n")
        raise SystemExit(1)
    try:
        info = beeper.server_info()
    except (beeper.UnreachableError, beeper.ApiError):
        sys.stderr.write("Beeper Desktop is not running on localhost:23373; start it first.\n")
        raise SystemExit(1) from None
    app = info.get("app") if isinstance(info.get("app"), dict) else {}
    sys.stderr.write(f"Found {app.get('name', 'Beeper')} {app.get('version', '')}".rstrip() + "\n")
    sys.stderr.write(AUTH_HELP + f"{TOKEN_FILE} with mode 600.\n")
    token = getpass.getpass("Access token: ").strip()
    if not token:
        sys.stderr.write("No token entered; nothing was saved.\n")
        raise SystemExit(1)
    try:
        beeper.probe(token)
    except beeper.AuthError:
        sys.stderr.write("Beeper rejected that token; nothing was saved.\n")
        raise SystemExit(1) from None
    except (beeper.ApiError, beeper.UnreachableError) as exc:
        sys.stderr.write(f"Could not verify the token: {exc}\n")
        raise SystemExit(1) from None
    save_token(token)
    sys.stdout.write(f"Token saved to {TOKEN_FILE}. The panel picks it up on the next refresh.\n")


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] == "list":
        page = ""
        rest = args[1:] if args else []
        while rest:
            if rest[0] == "--page" and len(rest) > 1:
                page = rest[1]
                rest = rest[2:]
            elif rest[0] == "--limit" and len(rest) > 1:
                os.environ["OMARCHY_BEEPER_MAX"] = rest[1]
                rest = rest[2:]
            else:
                rest = rest[1:]
        cmd_list(page)
        return
    if args[0] == "read":
        if len(args) < 2:
            die("usage: omarchy-beeper read <id>")
        cmd_read(args[1])
        return
    if args[0] == "read-all":
        cmd_read_all()
        return
    if args[0] == "open":
        cmd_open(args[1] if len(args) > 1 else "")
        return
    if args[0] == "auth":
        cmd_auth()
        return
    if args[0] in ("-h", "--help", "help"):
        sys.stdout.write(HELP)
        return
    die("unknown command; try: omarchy-beeper --help")


if __name__ == "__main__":
    main()
