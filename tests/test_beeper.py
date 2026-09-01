from __future__ import annotations

import re
import unittest
from contextlib import redirect_stderr
from io import StringIO
from unittest.mock import patch

from support import (  # noqa: I001 - puts lib/ on sys.path
    FakeHttp,
    FakeResponse,
    account,
    capture_json,
    chat,
    fake_api,
    http_error,
    message,
    search_page,
    url_error,
)

import beeper
import cli
import common

TOKEN = "test-token"
SEARCH = "/v1/chats/search"
ACCOUNTS = "/v1/accounts"
FOCUS = "/v1/focus"


def messages_path(chat_id: str) -> str:
    import urllib.parse

    return f"/v1/chats/{urllib.parse.quote(chat_id, safe='')}/messages"


def read_path(chat_id: str) -> str:
    import urllib.parse

    return f"/v1/chats/{urllib.parse.quote(chat_id, safe='')}/read"


def wire(http: FakeHttp, chats: list[dict], accounts: list[dict] | None = None) -> FakeHttp:
    """Standard happy-path routing: one search page, snippets, marks."""
    http.route("GET", SEARCH, lambda call: search_page(chats))
    http.route("GET", ACCOUNTS, accounts if accounts is not None else [account("a1", "WhatsApp")])
    http.route("POST", FOCUS, {"success": True})
    for item in chats:
        chat_id = item["id"]
        http.route("GET", messages_path(chat_id), {"items": [message(chat_id)], "hasMore": False})
        http.route("POST", read_path(chat_id), item)
    return http


def run_list(http: FakeHttp, page: str = "", limit: int = 25) -> dict:
    with fake_api(http), patch.object(cli, "load_token", return_value=TOKEN), patch.dict(
        "os.environ", {"OMARCHY_BEEPER_MAX": str(limit)}, clear=False
    ):
        return capture_json(cli.cmd_list, page)


class UrlBindingTests(unittest.TestCase):
    def test_every_call_stays_on_the_local_api(self) -> None:
        self.assertTrue(beeper._url("/v1/accounts").startswith("http://localhost:23373/"))

    def test_refuses_paths_outside_v1(self) -> None:
        for bad in ("/oauth/token", "v1/accounts", "http://evil.test/v1/x", "//evil.test/v1/x"):
            with self.assertRaises(beeper.ApiError, msg=bad):
                beeper._url(bad)

    def test_booleans_and_lists_become_query_values(self) -> None:
        url = beeper._url("/v1/chats/search", {"unreadOnly": True, "includeMuted": False})
        self.assertIn("unreadOnly=true", url)
        self.assertIn("includeMuted=false", url)

    def test_redirects_are_not_followed(self) -> None:
        self.assertIsNone(
            beeper._NoRedirect().redirect_request(None, None, 302, "Found", {}, "http://evil.test/")
        )


class ListContractTests(unittest.TestCase):
    def test_filters_out_muted_archived_and_low_priority(self) -> None:
        http = wire(FakeHttp(), [chat("!a:x")])
        run_list(http)
        search = next(c for c in http.calls if c.path == SEARCH)
        self.assertEqual(search.one("unreadOnly"), "true")
        self.assertEqual(search.one("includeMuted"), "false")
        self.assertEqual(search.one("inbox"), "primary")

    def test_one_row_per_chat_newest_first(self) -> None:
        chats = [
            chat("!old:x", title="Old", last_activity="2026-02-01T00:00:00Z"),
            chat("!new:x", title="New", last_activity="2026-02-11T00:00:00Z"),
            chat("!mid:x", title="Mid", last_activity="2026-02-05T00:00:00Z"),
        ]
        payload = run_list(wire(FakeHttp(), chats))
        self.assertEqual([row["subject"] for row in payload["messages"]], ["New", "Mid", "Old"])
        self.assertEqual(payload["unread"], 3)

    def test_row_shape_matches_the_panel_contract(self) -> None:
        chats = [chat("!a:x", title="Ada  Lovelace\n", network="Signal")]
        payload = run_list(wire(FakeHttp(), chats))
        row = payload["messages"][0]
        self.assertEqual(
            sorted(row),
            ["from", "id", "labels", "snippet", "subject", "threadId", "ts", "url"],
        )
        self.assertEqual(row["subject"], "Ada Lovelace")
        self.assertEqual(row["threadId"], "!a:x")
        self.assertEqual(row["id"], common.encode_id("!a:x"))
        self.assertEqual(common.decode_id(row["id"]), "!a:x")
        self.assertEqual(row["labels"], ["Signal"])
        self.assertEqual(row["from"], "Ada")
        self.assertEqual(row["snippet"], "are you coming tonight?")
        self.assertEqual(row["url"], "")
        self.assertEqual(row["ts"], 1770804000)  # 2026-02-11T10:00:00Z

    def test_unread_is_the_pile_not_the_page(self) -> None:
        chats = [chat(f"!c{i}:x", last_activity=f"2026-02-11T10:{i:02d}:00Z") for i in range(40)]
        payload = run_list(wire(FakeHttp(), chats), limit=25)
        self.assertEqual(payload["unread"], 40)
        self.assertEqual(len(payload["messages"]), 25)
        self.assertEqual(payload["thisPage"], "0")
        self.assertEqual(payload["nextPage"], "25")

    def test_second_page_is_the_offset_slice(self) -> None:
        # Descending activity, so c0 is newest and the offset slice starts at c25.
        chats = [
            chat(f"!c{i}:x", title=f"c{i}", last_activity=f"2026-02-11T10:{59 - i:02d}:00Z")
            for i in range(40)
        ]
        payload = run_list(wire(FakeHttp(), chats), page="25", limit=25)
        self.assertEqual(payload["thisPage"], "25")
        self.assertEqual(len(payload["messages"]), 15)
        self.assertEqual(payload["messages"][0]["subject"], "c25")
        self.assertEqual(payload["nextPage"], "")

    def test_bad_page_token_falls_back_to_the_first_page(self) -> None:
        payload = run_list(wire(FakeHttp(), [chat("!a:x")]), page="../etc/passwd")
        self.assertEqual(payload["thisPage"], "0")
        self.assertEqual(len(payload["messages"]), 1)

    def test_snippets_are_only_fetched_for_the_visible_page(self) -> None:
        chats = [chat(f"!c{i}:x", last_activity=f"2026-02-11T10:{i:02d}:00Z") for i in range(40)]
        http = wire(FakeHttp(), chats)
        run_list(http, limit=5)
        message_calls = [p for p in http.paths("GET") if p.endswith("/messages")]
        self.assertEqual(len(message_calls), 5)

    def test_a_failed_snippet_only_loses_the_snippet(self) -> None:
        chats = [chat("!a:x")]
        http = wire(FakeHttp(), chats)
        http.route("GET", messages_path("!a:x"), lambda call: http_error(500, "boom"))
        payload = run_list(http)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["messages"][0]["snippet"], "")
        self.assertEqual(payload["messages"][0]["subject"], "Ada Lovelace")
        self.assertEqual(payload["messages"][0]["ts"], 1770804000)

    def test_media_messages_get_a_placeholder_snippet(self) -> None:
        http = wire(FakeHttp(), [chat("!a:x")])
        http.route(
            "GET",
            messages_path("!a:x"),
            {"items": [message("!a:x", text="", kind="IMAGE")], "hasMore": False},
        )
        payload = run_list(http)
        self.assertEqual(payload["messages"][0]["snippet"], "[photo]")

    def test_newest_undeleted_message_wins(self) -> None:
        http = wire(FakeHttp(), [chat("!a:x")])
        items = [
            message("!a:x", text="older", timestamp="2026-02-11T09:00:00Z", sort_key="1"),
            message("!a:x", text="newest", timestamp="2026-02-11T11:00:00Z", sort_key="3"),
            dict(
                message("!a:x", text="deleted", timestamp="2026-02-11T12:00:00Z", sort_key="4"),
                isDeleted=True,
            ),
            dict(message("!a:x", text="👍", timestamp="2026-02-11T13:00:00Z", sort_key="5"), type="REACTION"),
        ]
        http.route("GET", messages_path("!a:x"), {"items": items, "hasMore": False})
        payload = run_list(http)
        self.assertEqual(payload["messages"][0]["snippet"], "newest")

    def test_inboxes_and_account_count_come_from_accounts(self) -> None:
        chats = [
            chat("!a:x", account="wa", network="WhatsApp"),
            chat("!b:x", account="wa", network="WhatsApp"),
            chat("!c:x", account="slack", network="Slack"),
        ]
        accounts = [account("wa", "WhatsApp"), account("slack", "Slack"), account("sig", "Signal")]
        payload = run_list(wire(FakeHttp(), chats, accounts))
        self.assertEqual(payload["accountCount"], 3)
        self.assertEqual(
            payload["inboxes"],
            [
                {"account": "WhatsApp", "unread": 2, "searchUrl": ""},
                {"account": "Slack", "unread": 1, "searchUrl": ""},
                {"account": "Signal", "unread": 0, "searchUrl": ""},
            ],
        )

    def test_chats_on_unknown_accounts_are_still_counted(self) -> None:
        chats = [chat("!a:x", account="ghost", network="Ghost")]
        payload = run_list(wire(FakeHttp(), chats, [account("wa", "WhatsApp")]))
        self.assertEqual(payload["inboxes"][-1], {"account": "Other", "unread": 1, "searchUrl": ""})

    def test_caught_up_is_a_success_with_an_empty_pile(self) -> None:
        payload = run_list(wire(FakeHttp(), []))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["unread"], 0)
        self.assertEqual(payload["messages"], [])
        self.assertEqual(payload["nextPage"], "")


class PaginationWalkTests(unittest.TestCase):
    def test_walks_cursors_until_the_api_runs_out(self) -> None:
        pages = [
            search_page([chat(f"!p1-{i}:x") for i in range(3)], has_more=True, cursor="cur1"),
            search_page([chat(f"!p2-{i}:x") for i in range(2)], has_more=False, cursor=None),
        ]
        seen: list[str] = []

        def handler(call):
            seen.append(call.one("cursor"))
            return pages[len(seen) - 1]

        http = FakeHttp({("GET", SEARCH): handler})
        with fake_api(http):
            chats, truncated = beeper.unread_chats(TOKEN)
        self.assertEqual(seen, ["", "cur1"])
        self.assertEqual(len(chats), 5)
        self.assertFalse(truncated)

    def test_walk_stops_at_the_fetch_cap_and_reports_truncation(self) -> None:
        def handler(call):
            return search_page(
                [chat(f"!c{call.one('cursor', '0')}-{i}:x") for i in range(150)],
                has_more=True,
                cursor="next",
            )

        http = FakeHttp({("GET", SEARCH): handler})
        with fake_api(http):
            chats, truncated = beeper.unread_chats(TOKEN, cap=common.FETCH_CAP)
        self.assertEqual(len(chats), common.FETCH_CAP)
        self.assertTrue(truncated)

    def test_truncated_walk_warns_in_the_list_payload(self) -> None:
        def handler(call):
            return search_page(
                [chat(f"!c{call.one('cursor', '0')}-{i}:x") for i in range(200)],
                has_more=True,
                cursor="next",
            )

        http = wire(FakeHttp(), [])
        http.route("GET", SEARCH, handler)
        for i in range(200):
            cid = f"!c0-{i}:x"
            http.route("GET", messages_path(cid), {"items": [message(cid)], "hasMore": False})
        payload = run_list(http, limit=2)
        self.assertEqual(payload["unread"], common.FETCH_CAP)
        self.assertIn(str(common.FETCH_CAP), payload["warning"])

    def test_duplicate_chats_across_pages_are_collapsed(self) -> None:
        pages = [
            search_page([chat("!dup:x"), chat("!a:x")], has_more=True, cursor="cur1"),
            search_page([chat("!dup:x"), chat("!b:x")], has_more=False, cursor=None),
        ]
        state = {"n": 0}

        def handler(call):
            page = pages[state["n"]]
            state["n"] += 1
            return page

        http = FakeHttp({("GET", SEARCH): handler})
        with fake_api(http):
            chats, _ = beeper.unread_chats(TOKEN)
        self.assertEqual(len(chats), 3)


class ReadTests(unittest.TestCase):
    def test_read_marks_exactly_that_chat(self) -> None:
        http = wire(FakeHttp(), [chat("!a:x")])
        opaque = common.encode_id("!a:x")
        with fake_api(http), patch.object(cli, "load_token", return_value=TOKEN):
            payload = capture_json(cli.cmd_read, opaque)
        self.assertEqual(payload, {"ok": True})
        self.assertEqual(http.paths("POST"), [read_path("!a:x")])

    def test_read_refuses_a_forged_id(self) -> None:
        http = FakeHttp()
        with fake_api(http), patch.object(cli, "load_token", return_value=TOKEN):
            payload = capture_json(cli.cmd_read, "beeper:!!!")
        self.assertFalse(payload["ok"])
        self.assertEqual(http.calls, [])

    def test_read_reports_an_api_failure(self) -> None:
        http = wire(FakeHttp(), [chat("!a:x")])
        http.route("POST", read_path("!a:x"), lambda call: http_error(500, "bridge offline"))
        with fake_api(http), patch.object(cli, "load_token", return_value=TOKEN):
            payload = capture_json(cli.cmd_read, common.encode_id("!a:x"))
        self.assertFalse(payload["ok"])
        self.assertIn("500", payload["error"])


class ReadAllTests(unittest.TestCase):
    def _chats(self, n: int) -> list[dict]:
        return [chat(f"!c{i}:x", last_activity=f"2026-02-11T10:{i % 60:02d}:00Z") for i in range(n)]

    def _run(self, http: FakeHttp) -> dict:
        with fake_api(http), patch.object(cli, "load_token", return_value=TOKEN):
            return capture_json(cli.cmd_read_all)

    def test_snapshots_before_marking(self) -> None:
        chats = self._chats(3)
        http = wire(FakeHttp(), chats)
        payload = self._run(http)
        self.assertEqual(payload, {"ok": True, "marked": 3})
        methods = [c.method for c in http.calls]
        self.assertEqual(methods[0], "GET")
        self.assertEqual(http.calls[0].path, SEARCH)
        self.assertNotIn("GET", methods[1:])

    def test_walks_past_the_display_cap(self) -> None:
        chats = self._chats(300)
        http = wire(FakeHttp(), chats)
        payload = self._run(http)
        self.assertEqual(payload, {"ok": True, "marked": 300})
        self.assertEqual(len([c for c in http.calls if c.method == "POST"]), 300)

    def test_partial_failure_keeps_the_marked_count(self) -> None:
        chats = self._chats(4)
        http = wire(FakeHttp(), chats)
        http.route("POST", read_path("!c2:x"), lambda call: http_error(500, "bridge offline"))
        payload = self._run(http)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["marked"], 3)
        self.assertIn("500", payload["error"])

    def test_budget_stops_the_run_with_a_partial_count(self) -> None:
        chats = self._chats(5)
        http = wire(FakeHttp(), chats)
        with patch.object(cli, "READ_ALL_BUDGET", 0.0), patch.object(cli, "READ_ALL_WORKERS", 1):
            payload = self._run(http)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["marked"], 0)
        self.assertIn("stopped after", payload["error"])

    def test_nothing_unread_is_a_no_op(self) -> None:
        payload = self._run(wire(FakeHttp(), []))
        self.assertEqual(payload, {"ok": True, "marked": 0})

    def test_a_failed_snapshot_does_not_mark_anything(self) -> None:
        http = FakeHttp({("GET", SEARCH): lambda call: http_error(500, "index rebuilding")})
        payload = self._run(http)
        self.assertFalse(payload["ok"])
        self.assertEqual(http.paths("POST"), [])


class OpenTests(unittest.TestCase):
    def test_open_focuses_the_chat(self) -> None:
        http = wire(FakeHttp(), [chat("!a:x")])
        with fake_api(http), patch.object(cli, "load_token", return_value=TOKEN):
            payload = capture_json(cli.cmd_open, common.encode_id("!a:x"))
        self.assertEqual(payload, {"ok": True})
        focus = next(c for c in http.calls if c.path == FOCUS)
        self.assertEqual(focus.body, {"chatID": "!a:x"})

    def test_open_without_an_id_only_focuses_the_app(self) -> None:
        http = wire(FakeHttp(), [])
        with fake_api(http), patch.object(cli, "load_token", return_value=TOKEN):
            payload = capture_json(cli.cmd_open, "")
        self.assertEqual(payload, {"ok": True})
        focus = next(c for c in http.calls if c.path == FOCUS)
        self.assertEqual(focus.body, {})

    def test_the_only_writes_are_mark_read_and_focus(self) -> None:
        source = (common.ROOT / "lib" / "beeper.py").read_text(encoding="utf-8")
        mutations = re.findall(
            r'"(POST|PUT|PATCH|DELETE)",\s*(?:#[^\n]*\n\s*)?(f?"[^"]+")', source
        )
        self.assertTrue(mutations, "expected to find the mutating calls")
        for method, path in mutations:
            self.assertTrue(
                path.endswith('/read"') or path == '"/v1/focus"',
                msg=f"unexpected write: {method} {path}",
            )


class FailureMappingTests(unittest.TestCase):
    def test_401_asks_for_auth(self) -> None:
        http = FakeHttp({("GET", SEARCH): lambda call: http_error(401, "Unauthorized")})
        with fake_api(http), patch.object(cli, "load_token", return_value=TOKEN):
            payload = capture_json(cli.cmd_list, "")
        self.assertEqual(payload["error"], "token missing or invalid; run: omarchy-beeper auth")

    def test_connection_refused_says_beeper_is_not_running(self) -> None:
        http = FakeHttp({("GET", SEARCH): lambda call: url_error()})
        with fake_api(http), patch.object(cli, "load_token", return_value=TOKEN):
            payload = capture_json(cli.cmd_list, "")
        self.assertEqual(payload["error"], "Beeper Desktop is not running")

    def test_missing_token_is_told_apart_from_a_dead_app(self) -> None:
        http = FakeHttp({("GET", "/v1/info"): {"app": {"name": "Beeper"}}})
        with fake_api(http), patch.object(cli, "load_token", return_value=""):
            payload = capture_json(cli.cmd_list, "")
        self.assertEqual(payload["error"], "token missing or invalid; run: omarchy-beeper auth")

        http = FakeHttp({("GET", "/v1/info"): lambda call: url_error()})
        with fake_api(http), patch.object(cli, "load_token", return_value=""):
            payload = capture_json(cli.cmd_list, "")
        self.assertEqual(payload["error"], "Beeper Desktop is not running")

    def test_oversized_response_is_refused_before_decode(self) -> None:
        huge = b'{"items":[' + b" " * (common.MAX_HTTP_BODY + 10) + b"]}"
        http = FakeHttp({("GET", SEARCH): FakeResponse(huge, content_length=False)})
        with fake_api(http), patch.object(cli, "load_token", return_value=TOKEN):
            payload = capture_json(cli.cmd_list, "")
        self.assertFalse(payload["ok"])
        self.assertIn("too large", payload["error"])

    def test_declared_oversized_response_is_refused_without_reading(self) -> None:
        http = FakeHttp(
            {("GET", SEARCH): FakeResponse({"items": []}, content_length=common.MAX_HTTP_BODY + 1)}
        )
        with fake_api(http), patch.object(cli, "load_token", return_value=TOKEN):
            payload = capture_json(cli.cmd_list, "")
        self.assertFalse(payload["ok"])

    def test_error_bodies_are_capped(self) -> None:
        self.assertEqual(beeper.MAX_HTTP_ERROR, 64 * 1024)

    def test_invalid_json_is_reported_not_raised(self) -> None:
        http = FakeHttp({("GET", SEARCH): FakeResponse(b"not json")})
        with fake_api(http), patch.object(cli, "load_token", return_value=TOKEN):
            payload = capture_json(cli.cmd_list, "")
        self.assertFalse(payload["ok"])
        self.assertIn("invalid JSON", payload["error"])

    def test_api_error_text_is_one_line(self) -> None:
        http = FakeHttp({("GET", SEARCH): lambda call: http_error(503, "a\nb\n   c")})
        with fake_api(http), patch.object(cli, "load_token", return_value=TOKEN):
            payload = capture_json(cli.cmd_list, "")
        self.assertNotIn("\n", payload["error"])
        self.assertIn("a b c", payload["error"])


class CliSurfaceTests(unittest.TestCase):
    def test_unknown_command_still_prints_one_json_object(self) -> None:
        payload = capture_json(cli.main, ["frobnicate"])
        self.assertFalse(payload["ok"])
        self.assertIn("unknown command", payload["error"])

    def test_read_without_an_id_is_a_usage_error(self) -> None:
        payload = capture_json(cli.main, ["read"])
        self.assertFalse(payload["ok"])

    def test_limit_flag_sets_the_page_size(self) -> None:
        http = wire(FakeHttp(), [chat(f"!c{i}:x", last_activity=f"2026-02-11T10:{i:02d}:00Z") for i in range(10)])
        with fake_api(http), patch.object(cli, "load_token", return_value=TOKEN):
            payload = capture_json(cli.main, ["list", "--limit", "3"])
        self.assertEqual(len(payload["messages"]), 3)

    def test_auth_refuses_a_non_terminal(self) -> None:
        stderr = StringIO()
        with patch("sys.stdin.isatty", return_value=False), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                cli.cmd_auth()
        self.assertEqual(raised.exception.code, 1)
        self.assertIn("needs a terminal", stderr.getvalue())

    def test_the_token_never_reaches_argv_or_the_shell(self) -> None:
        bin_script = (common.ROOT / "bin" / "omarchy-beeper").read_text(encoding="utf-8")
        for banned in ("--token", "$TOKEN", "token.json", "secrets"):
            self.assertNotIn(banned, bin_script)
        cli_source = (common.ROOT / "lib" / "cli.py").read_text(encoding="utf-8")
        # Read from a hidden prompt, and only ever handed to the API client.
        self.assertIn("getpass.getpass", cli_source)
        self.assertNotIn("input(", cli_source)
        panel = (common.ROOT / "Panel.qml").read_text(encoding="utf-8")
        for banned in ("Authorization", "token.json", "secrets", "XMLHttpRequest", "WebSocket"):
            self.assertNotIn(banned, panel)
        api = (common.ROOT / "lib" / "beeper.py").read_text(encoding="utf-8")
        self.assertEqual(api.count('add_header("Authorization"'), 1)


if __name__ == "__main__":
    unittest.main()
