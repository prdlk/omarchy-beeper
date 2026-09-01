# Changelog

## 1.0.0

First release.

- Bar icon: stroked chat bubble with an unread-chat badge in the bar
  foreground colour, dimmed to half opacity when Beeper Desktop is unreachable.
- Panel: one row per unread chat across every connected network, newest first,
  with the network as a chip when more than one account is connected.
- Click a row (or `Enter`/`Space`/`o`) to focus that chat in Beeper Desktop and
  mark it read; the row disappears immediately and the mark is queued.
- `a` marks the row under the cursor read without opening it. `A` marks every
  unread chat read behind a two-press confirm, with a 100-second budget and a
  partial count.
- Paging with `n`/`p`, page size 1–50, poll interval 15–3600 s.
- `bin/omarchy-beeper` CLI: `list`, `read`, `read-all`, `open`, `auth`. Every
  command prints one JSON object and exits 0.
- Muted, archived, and low-priority chats are excluded from the pile, filtered
  on each chat's own flags: Beeper Desktop 4.3.73 ignores `includeMuted=false`
  and `inbox=primary` hides genuinely unread chats when combined with
  `unreadOnly=true`.
- Row snippets come from `GET /v1/messages/search` (~1 ms per chat) with
  `GET /v1/chats/{chatID}/messages` as the fallback for chats the message
  index has not reached yet, and only for the rows on screen.
- Token stored at `~/.config/omarchy-beeper/secrets/token.json` (600, in a 700
  directory), read with `O_NOFOLLOW`, never passed in argv.
- All HTTP bound to `http://localhost:23373/v1/`, no redirects, 2 MiB body cap
  and 64 KiB error cap, 30 s per request.
- Read and mark-read only: the plugin never sends a message.
