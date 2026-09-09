# mcp-silverbullet

An MCP server that exposes a remote [SilverBullet](https://silverbullet.md) space
to an MCP client, over SilverBullet's `/.fs` HTTP API.

Reads are unrestricted across the space; writes are confined to a configurable
page prefix.

## Requirements

- Python 3.10+
- A SilverBullet instance and an API token (admin UI > Users > API tokens)

## Install

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Configuration

| Variable          | Required | Default   | Description                                                  |
| ----------------- | -------- | --------- | ------------------------------------------------------------ |
| `SB_BASE_URL`     | yes      | —         | Space URL including its prefix, e.g. `https://notes.example.fr/work` |
| `SB_TOKEN`        | yes      | —         | API token used for every request                              |
| `SB_WRITE_PREFIX` | no       | `Inbox/`  | Page prefix under which writing is allowed                    |
| `HOST`            | no       | `127.0.0.1` | Interface the HTTP transport binds to                       |
| `PORT`            | no       | `8000`    | Port the HTTP transport listens on                            |

## Run

```sh
export SB_BASE_URL=https://notes.example.fr/work
export SB_TOKEN=...
.venv/bin/python sb_mcp_http.py
```

The server speaks MCP over the `streamable-http` transport, on `/mcp`. It binds
to `127.0.0.1` by default, so a client on the same machine reaches it at
`http://127.0.0.1:8000/mcp`.

To reach it from anywhere else, put a reverse proxy in front of it rather than
changing `HOST` — see [Deployment](#deployment).

## Tools

| Tool             | Description                                                        |
| ---------------- | ------------------------------------------------------------------ |
| `list_pages`     | All pages in the space, most recently modified first                |
| `read_page`      | Markdown body of one page, named without the `.md` extension        |
| `search_pages`   | Case-insensitive search over page names and bodies                  |
| `create_note`    | Create a page, failing if it already exists                         |
| `append_to_note` | Append text to an existing page without overwriting the rest        |

Both write tools reject any page outside `SB_WRITE_PREFIX`, and both use HTTP
conditional requests — `If-None-Match` on create, `If-Match` on append — so a
page that changed underneath the server is not clobbered.

## Deployment

**The server has no authentication of its own.** Any request that reaches it
gets read access to the entire space, plus write access under
`SB_WRITE_PREFIX`, using the configured token. That is why it binds to the
loopback interface: authentication and TLS belong to a reverse proxy in front
of it.

[`Caddyfile.example`](Caddyfile.example) is a working starting point. The one
non-obvious setting is `flush_interval -1`: MCP streams its responses as
server-sent events, and a proxy that buffers them leaves the client waiting
forever.

Setting `HOST=0.0.0.0` publishes an unauthenticated server on every interface.
Only do it where something else restricts access — a container published as
`-p 127.0.0.1:8000:8000`, or a private network you control.

Note that MCP's own HTTP auth is OAuth bearer-based, so a given client may not
support HTTP Basic. If yours does not, either use a proxy that accepts a bearer
token, or move authentication into the server itself: the SDK takes a
`token_verifier` on `MCPServer(...)` for exactly this.

## Known limitations

`search_pages` fetches every page in the space on each call, so search cost
grows linearly with space size.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
