# Setup

## 1. Turn on the Beeper Desktop API

Beeper Desktop serves a local-only HTTP API on `http://localhost:23373`. Check
it is up — this endpoint needs no token:

```bash
curl -s http://localhost:23373/v1/info
```

You should get JSON describing the app and `"status": "running"`. If the
connection is refused, Beeper Desktop is not running, or the API is disabled in
its settings.

## 2. Create an access token

In Beeper Desktop: **Settings → Integrations → create an access token.**

Read access is all this plugin needs. It calls exactly six endpoints:

| Call | Why |
| --- | --- |
| `GET /v1/info` | Tell "Beeper is down" apart from "the token is wrong" |
| `GET /v1/accounts` | Network names for the chips, and the account count |
| `GET /v1/chats/search` | The unread pile |
| `GET /v1/chats/{chatID}/messages` | The last message, for the row snippet |
| `POST /v1/chats/{chatID}/read` | Mark a chat read |
| `POST /v1/focus` | Bring Beeper Desktop forward on a chat |

Nothing else. No sends, no edits, no reactions, no drafts.

## 3. Store the token

```bash
~/.config/omarchy/plugins/prdlk.beeper/bin/omarchy-beeper auth
```

The command refuses to run unless stdin and stderr are both terminals, so a
token can never be piped in from a script or a shell history. It reads the
token with a hidden prompt, verifies it with one `GET /v1/accounts`, and only
then writes it to:

```
~/.config/omarchy-beeper/secrets/token.json   # mode 600
~/.config/omarchy-beeper/secrets/            # mode 700
```

The write is atomic: a temp file in the same directory, `fchmod` 600, `fsync`,
`rename`, then `fsync` on the directory.

## 4. Check it

```bash
~/.config/omarchy/plugins/prdlk.beeper/bin/omarchy-beeper list --limit 5
```

A healthy answer starts with `{"ok": true, "unread": …}`. Then click the bar
icon.

## Troubleshooting

| Message | Meaning |
| --- | --- |
| `Beeper Desktop is not running` | Nothing is listening on `localhost:23373`. Start Beeper. |
| `token missing or invalid; run: omarchy-beeper auth` | No token stored, or Beeper rejected it (401/403). Re-run `auth`. |
| `secret file for beeper is too open; chmod 600 it` | Someone widened the token file. `chmod 600 ~/.config/omarchy-beeper/secrets/token.json`. |
| `secret directory for beeper is too open; chmod 700 it` | `chmod 700 ~/.config/omarchy-beeper/secrets`. |
| `showing the newest 200 unread chats` | The pile is deeper than the display cap. Use `A` to clear it. |
| Snippets are blank | Beeper is still indexing, or that chat has only media. Titles and counts are still correct. |
| `python3 is required` | The bar could not find `python3` on `PATH`. |

The bar is not a login shell, so `bin/omarchy-beeper` only extends `PATH` with
`~/.local/share/mise/shims` and `~/.local/bin`. If your `python3` lives
somewhere else, symlink it into `~/.local/bin`.

## Removing everything

```bash
omarchy plugin remove prdlk.beeper
rm -rf ~/.config/omarchy-beeper
```

Then revoke the token in Beeper Desktop → Settings → Integrations. Removing the
plugin does not revoke it for you.
