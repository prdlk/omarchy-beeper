"""Shared helpers for omarchy-beeper. No secrets are printed.

Ported from omarchy-you-got-mail: the file-handling and body-cap rules are
identical, only the config directory and the id shape changed.
"""

from __future__ import annotations

import base64
import errno
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "omarchy-beeper"
SECRETS_DIR = CONFIG_DIR / "secrets"
TOKEN_FILE = SECRETS_DIR / "token.json"

# Only the local Beeper Desktop API. Nothing else is reachable from here.
API_HOST = "localhost"
API_PORT = 23373
API_BASE = f"http://{API_HOST}:{API_PORT}"

PAGE_SIZE_MAX = 50
FETCH_CAP = 200
MAX_HTTP_BODY = 2 * 1024 * 1024
MAX_HTTP_ERROR = 64 * 1024
MAX_LOCAL_FILE = 64 * 1024

# Beeper chat ids are Matrix-ish: "!room:server", or a numeric local chat id.
CHAT_ID_RE = re.compile(r"^[!@#$+]?[A-Za-z0-9._=/+-]{1,255}(?::[A-Za-z0-9._-]{1,255})?$")
ID_PREFIX = "beeper"


class ResponseTooLargeError(ValueError):
    def __init__(self, message: str = "response too large") -> None:
        super().__init__(message)


class FileTooLargeError(ValueError):
    def __init__(self, message: str = "file too large") -> None:
        super().__init__(message)


def _declared_length(fp: object) -> int | None:
    headers = getattr(fp, "headers", None)
    if headers is None or not hasattr(headers, "get"):
        return None
    raw = headers.get("Content-Length")
    if raw is None:
        return None
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if n < 0:
        return None
    return n


def read_http_body(fp: object, limit: int = MAX_HTTP_BODY) -> bytes:
    declared = _declared_length(fp)
    if declared is not None and declared > limit:
        raise ResponseTooLargeError()
    read = getattr(fp, "read")
    data = read(limit + 1)
    if not isinstance(data, (bytes, bytearray)):
        data = bytes(data)
    if len(data) > limit:
        raise ResponseTooLargeError()
    return bytes(data)


def die(message: str, code: int = 0) -> None:
    sys.stdout.write(json.dumps({"ok": False, "error": message}, ensure_ascii=False) + "\n")
    raise SystemExit(code)


def emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")


def read_owned_file(
    path: Path,
    limit: int = MAX_LOCAL_FILE,
    *,
    require_private: bool = False,
) -> bytes | None:
    """Open path once, validate the fd, and read at most *limit* bytes.

    Uses O_NOFOLLOW|O_NONBLOCK so a swapped symlink or FIFO cannot redirect
    or block the read. Missing paths, dangling/final-component symlinks, and
    non-regular files return None. Wrong owner is None unless require_private
    is set, in which case PermissionError is raised (and too-open mode too).
    """
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENXIO, errno.EAGAIN, errno.EWOULDBLOCK, errno.ENOTDIR):
            return None
        raise
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None
        if st.st_uid != os.getuid():
            if require_private:
                raise PermissionError("not owned")
            return None
        if require_private and st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise PermissionError("too open")
        if st.st_size > limit:
            raise FileTooLargeError()
        data = os.read(fd, limit + 1)
        if len(data) > limit:
            raise FileTooLargeError()
        return data
    finally:
        os.close(fd)


def _config_value(key: str) -> str | None:
    path = CONFIG_DIR / "config"
    try:
        raw = read_owned_file(path)
    except (OSError, FileTooLargeError, PermissionError):
        return None
    if raw is None:
        return None
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, rest = stripped.partition("=")
        if name.strip().lower() == key:
            return rest.strip().strip("\"'")
    return None


def clamp_int(raw: object, default: int, lo: int, hi: int) -> int:
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def max_chats() -> int:
    raw = os.environ.get("OMARCHY_BEEPER_MAX") or _config_value("max") or "25"
    return clamp_int(raw, 25, 1, PAGE_SIZE_MAX)


def fetch_limit(raw: object | None = None) -> int:
    if raw is None:
        raw = os.environ.get("OMARCHY_BEEPER_FETCH") or os.environ.get("OMARCHY_BEEPER_MAX") or "25"
    return clamp_int(raw, 25, 1, FETCH_CAP)


def one_line(value: str, limit: int = 180) -> str:
    text = re.sub(r"[\u00ad\u034f\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]+", "", value or "")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def ensure_config_dirs() -> None:
    CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)
    SECRETS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(SECRETS_DIR, 0o700)


def write_private(path: Path, text: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    replaced = False
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fd = -1
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        replaced = True
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        dirfd = os.open(str(path.parent), flags)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
        os.chmod(path, 0o600)
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        if not replaced:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def load_secret_file(path: Path, label: str = "") -> dict:
    label = label or path.name
    try:
        raw = read_owned_file(path, require_private=True)
    except FileTooLargeError:
        die(f"secret file for {label} is too large")
    except PermissionError as exc:
        reason = str(exc)
        if "too open" in reason:
            die(f"secret file for {label} is too open; chmod 600 it")
        if "not owned" in reason:
            die(f"secret file for {label} is not owned by you")
        die(f"secret file for {label} is not readable")
    except OSError:
        die(f"secret file for {label} is not readable")
    if raw is None:
        return {}
    parent = path.parent
    if parent.is_symlink():
        die(f"secret directory for {label} is a symlink")
    try:
        pst = parent.stat()
    except OSError:
        die(f"secret directory for {label} is not readable")
    if pst.st_uid != os.getuid() or pst.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        die(f"secret directory for {label} is too open; chmod 700 it")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        die(f"secret file for {label} is not valid JSON")
    return data if isinstance(data, dict) else {}


def load_token() -> str:
    """Read the access token. Empty string means 'not set up yet'."""
    data = load_secret_file(TOKEN_FILE, "beeper")
    token = data.get("token")
    return str(token) if isinstance(token, str) else ""


def save_token(token: str) -> None:
    ensure_config_dirs()
    write_private(TOKEN_FILE, json.dumps({"token": token}, indent=2) + "\n")


def encode_id(chat_id: str) -> str:
    """One row per chat, so the opaque id carries only the chat id."""
    blob = base64.urlsafe_b64encode(chat_id.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{ID_PREFIX}:{blob}"


def decode_id(opaque: str) -> str:
    prefix, _, blob = str(opaque).partition(":")
    if prefix != ID_PREFIX or not blob:
        die("not a chat id")
    pad = "=" * ((4 - len(blob) % 4) % 4)
    try:
        chat_id = base64.urlsafe_b64decode(blob + pad).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        die("not a chat id")
    if not CHAT_ID_RE.match(chat_id):
        die("not a chat id")
    return chat_id
