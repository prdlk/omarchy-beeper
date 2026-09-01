from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from support import ROOT, capture_json  # noqa: I001 - puts lib/ on sys.path

import common


def _workdir() -> Path:
    # Outside the plugin folder: `omarchy plugin validate` rejects symlinks
    # anywhere inside it, and one of these tests plants one on purpose.
    return Path(tempfile.mkdtemp(prefix="omarchy-beeper-tests."))


class HelperTests(unittest.TestCase):
    def test_clamp_int(self) -> None:
        self.assertEqual(common.clamp_int("25", 10, 1, 50), 25)
        self.assertEqual(common.clamp_int("0", 10, 1, 50), 1)
        self.assertEqual(common.clamp_int("99", 10, 1, 50), 50)
        self.assertEqual(common.clamp_int("nope", 10, 1, 50), 10)
        self.assertEqual(common.clamp_int(None, 7, 1, 50), 7)

    def test_max_chats_clamps_to_page_size(self) -> None:
        with patch.dict(os.environ, {"OMARCHY_BEEPER_MAX": "500"}, clear=False):
            self.assertEqual(common.max_chats(), common.PAGE_SIZE_MAX)
        with patch.dict(os.environ, {"OMARCHY_BEEPER_MAX": "0"}, clear=False):
            self.assertEqual(common.max_chats(), 1)

    def test_fetch_limit_caps_at_200(self) -> None:
        self.assertEqual(common.fetch_limit("200"), 200)
        self.assertEqual(common.fetch_limit("999"), 200)
        self.assertEqual(common.fetch_limit("0"), 1)

    def test_one_line_collapses_and_truncates(self) -> None:
        self.assertEqual(common.one_line("Hello\r\nworld\t  there"), "Hello world there")
        self.assertEqual(common.one_line("zero\u200bwidth"), "zerowidth")
        long = common.one_line("x" * 400)
        self.assertEqual(len(long), 180)
        self.assertTrue(long.endswith("…"))

    def test_encode_decode_chat_id(self) -> None:
        opaque = common.encode_id("!NCdzlIaMjZUmvmvyHU:beeper.com")
        self.assertTrue(opaque.startswith("beeper:"))
        self.assertNotIn("=", opaque)
        self.assertEqual(common.decode_id(opaque), "!NCdzlIaMjZUmvmvyHU:beeper.com")

    def test_encode_decode_survives_slashes_and_plus(self) -> None:
        raw = "!a+b/c=d:local-whatsapp.localhost"
        self.assertEqual(common.decode_id(common.encode_id(raw)), raw)

    def test_decode_id_rejects_junk(self) -> None:
        for bad in ("", "nope", "beeper:", "other:aGk", "beeper:!!!!", "beeper:" + "A" * 4):
            payload = capture_json(common.decode_id, bad)
            self.assertFalse(payload["ok"], msg=bad)
            self.assertEqual(payload["error"], "not a chat id")

    def test_api_base_is_local_only(self) -> None:
        self.assertEqual(common.API_BASE, "http://localhost:23373")


class HttpBodyTests(unittest.TestCase):
    def _body(self, data: bytes, content_length: object | None = ""):
        class Body:
            def __init__(self) -> None:
                self._buf = BytesIO(data)
                self.headers = {}
                if content_length != "":
                    self.headers["Content-Length"] = content_length

            def read(self, n: int = -1) -> bytes:
                return self._buf.read(n)

        return Body()

    def test_accepts_body_at_limit(self) -> None:
        raw = common.read_http_body(self._body(b"a" * 16), 16)
        self.assertEqual(raw, b"a" * 16)

    def test_rejects_body_over_limit(self) -> None:
        with self.assertRaises(common.ResponseTooLargeError):
            common.read_http_body(self._body(b"a" * 17), 16)

    def test_rejects_declared_length_over_limit(self) -> None:
        with self.assertRaises(common.ResponseTooLargeError):
            common.read_http_body(self._body(b"a", content_length="99999"), 16)

    def test_ignores_bogus_declared_length(self) -> None:
        raw = common.read_http_body(self._body(b"ab", content_length="not-a-number"), 16)
        self.assertEqual(raw, b"ab")

    def test_default_caps_are_the_documented_ones(self) -> None:
        self.assertEqual(common.MAX_HTTP_BODY, 2 * 1024 * 1024)
        self.assertEqual(common.MAX_HTTP_ERROR, 64 * 1024)
        self.assertEqual(common.MAX_LOCAL_FILE, 64 * 1024)


class SecretFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = _workdir()
        self.path = self.dir / "token.json"
        self.addCleanup(shutil.rmtree, self.dir, True)

    def _write(self, text: str, mode: int = 0o600) -> None:
        self.path.write_text(text, encoding="utf-8")
        os.chmod(self.path, mode)
        os.chmod(self.dir, 0o700)

    def test_missing_token_file_is_empty_not_an_error(self) -> None:
        with patch.object(common, "TOKEN_FILE", self.path):
            self.assertEqual(common.load_token(), "")

    def test_reads_private_token(self) -> None:
        self._write(json.dumps({"token": "abc123"}))
        with patch.object(common, "TOKEN_FILE", self.path):
            self.assertEqual(common.load_token(), "abc123")

    def test_rejects_group_or_world_readable_token(self) -> None:
        self._write(json.dumps({"token": "abc123"}), mode=0o644)
        with patch.object(common, "TOKEN_FILE", self.path):
            payload = capture_json(common.load_token)
        self.assertFalse(payload["ok"])
        self.assertIn("too open", payload["error"])

    def test_rejects_too_open_secrets_directory(self) -> None:
        self._write(json.dumps({"token": "abc123"}))
        os.chmod(self.dir, 0o755)
        try:
            with patch.object(common, "TOKEN_FILE", self.path):
                payload = capture_json(common.load_token)
        finally:
            os.chmod(self.dir, 0o700)
        self.assertFalse(payload["ok"])
        self.assertIn("chmod 700", payload["error"])

    def test_rejects_invalid_json(self) -> None:
        self._write("{not json")
        with patch.object(common, "TOKEN_FILE", self.path):
            payload = capture_json(common.load_token)
        self.assertFalse(payload["ok"])
        self.assertIn("not valid JSON", payload["error"])

    def test_rejects_oversized_token_file(self) -> None:
        self._write("x" * (common.MAX_LOCAL_FILE + 1))
        with patch.object(common, "TOKEN_FILE", self.path):
            payload = capture_json(common.load_token)
        self.assertFalse(payload["ok"])
        self.assertIn("too large", payload["error"])

    def test_symlinked_token_file_is_not_followed(self) -> None:
        target = self.dir / "real.json"
        target.write_text(json.dumps({"token": "leak"}), encoding="utf-8")
        os.chmod(target, 0o600)
        link = self.dir / "link.json"
        link.symlink_to(target)
        with patch.object(common, "TOKEN_FILE", link):
            self.assertEqual(common.load_token(), "")

    def test_save_token_writes_600_and_survives_replace(self) -> None:
        with patch.object(common, "CONFIG_DIR", self.dir), patch.object(
            common, "SECRETS_DIR", self.dir
        ), patch.object(common, "TOKEN_FILE", self.path):
            common.save_token("first")
            common.save_token("second")
            self.assertEqual(common.load_token(), "second")
        mode = stat.S_IMODE(self.path.stat().st_mode)
        self.assertEqual(mode, 0o600)
        self.assertEqual(len(list(self.dir.glob("*.tmp"))), 0)


if __name__ == "__main__":
    unittest.main()
