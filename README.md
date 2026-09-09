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
| `PORT`            | no       | `8000`    | Port the HTTP transport listens on                            |

## Run

```sh
export SB_BASE_URL=https://notes.example.fr/work
export SB_TOKEN=...
.venv/bin/python sb_mcp_http.py
```

The server speaks MCP over the `streamable-http` transport. Point your client at
`http://<host>:<port>/mcp`.

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

## Security

**The server has no authentication of its own.** It listens on `0.0.0.0` and
anyone who can reach the port gets read access to the entire space, plus write
access under `SB_WRITE_PREFIX`, using the configured token. Bind it to
`127.0.0.1` or put an authenticating reverse proxy in front of it before
exposing it to a network.

`search_pages` fetches every page in the space on each call, so search cost
grows linearly with space size.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
