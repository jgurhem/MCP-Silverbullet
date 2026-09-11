"""MCP server exposing a remote SilverBullet space, over streamable-http.

The server has no authentication of its own: it listens on the loopback
interface and must stay behind a reverse proxy handling TLS and auth (see
Caddyfile.example). Do not set `host` to 0.0.0.0 without such a proxy.

Configuration comes from a TOML file, `config.toml` by default:

    base_url = "https://notes.example.fr/work"
    token = "..."

See config.toml.example for the optional keys.
"""

import sys
import asyncio
import tomllib
from dataclasses import dataclass, fields

import httpx
from mcp.server.mcpserver import MCPServer

TIMEOUT = httpx.Timeout(30.0)


@dataclass
class Config:
    """Settings of one SilverBullet space, and of the server exposing it.

    base_url: space URL, prefix included (e.g. https://notes.example.fr/work)
    token: account API token (admin UI > Users > API tokens)
    write_prefix: prefix under which writing is allowed
    hide_prefixes: prefixes excluded from listings and searches
    host, port: what the streamable-http transport binds to
    """

    base_url: str
    token: str
    write_prefix: str = "Inbox/"
    hide_prefixes: tuple[str, ...] = ("Library/",)
    host: str = "127.0.0.1"
    port: int = 8000

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        self.hide_prefixes = tuple(self.hide_prefixes)

    @classmethod
    def from_toml(cls, path: str) -> "Config":
        with open(path, "rb") as f:
            data = tomllib.load(f)
        known = {f.name for f in fields(cls)}
        # A typo in a key would silently fall back to the default, and for
        # hide_prefixes that default exposes pages meant to stay hidden.
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(f"{path}: unknown key(s): {', '.join(unknown)}")
        missing = sorted({"base_url", "token"} - set(data))
        if missing:
            raise ValueError(f"{path}: missing key(s): {', '.join(missing)}")
        return cls(**data)


def _page_path(name: str) -> str:
    name = name.strip().strip("/")
    if name.endswith(".md"):
        name = name[:-3]
    if ".." in name:
        raise ValueError("invalid page name")
    return f"{name}.md"


def _writable(name: str, write_prefix: str) -> str:
    path = _page_path(name)
    if not path.startswith(write_prefix):
        raise ValueError(
            f"writing allowed only under {write_prefix} (got: {path})"
        )
    return path


def _render(content: str, destination: str) -> str:
    """Formatting of a note to be filed: frontmatter and button that the
    space-lua `Meta/Inbox` page knows how to handle. Without a destination, the
    body goes through as is."""
    if not destination.strip():
        return content
    return (
        f"---\ndestination: {destination.strip()}\n---\n"
        f"${{inbox.button()}}\n\n{content.strip()}\n"
    )


def build_server(config: Config) -> MCPServer:
    """Builds the MCP server exposing the space described by `config`."""
    base = config.base_url
    headers = {"Authorization": f"Bearer {config.token}"}
    write_prefix = config.write_prefix
    hide_prefixes = config.hide_prefixes

    instructions = f"""Access to SilverBullet notes.

Reading covers the whole space, writing only under {write_prefix}.

A note meant for another page is still created with create_note, by filling in
`destination` (e.g. "Journal/2026-09-10"): it is dropped under {write_prefix}
with a *File* button the user clicks in SilverBullet to append the body to the
destination and delete the note. Never write that frontmatter by hand, the
server takes care of it."""

    mcp = MCPServer("silverbullet", instructions=instructions)

    async def _list_md() -> list[dict]:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(f"{base}/.fs", headers=headers)
            r.raise_for_status()
            return [
                f
                for f in r.json()
                if f["name"].endswith(".md")
                and not f["name"].startswith(hide_prefixes)
            ]

    @mcp.tool()
    async def list_pages() -> str:
        """Lists the pages of the space, most recently modified first.
        System pages (Library/ by default) are excluded."""
        files = await _list_md()
        files.sort(key=lambda f: f.get("lastModified", 0), reverse=True)
        return "\n".join(f["name"][:-3] for f in files) or "(empty space)"

    @mcp.tool()
    async def read_page(name: str) -> str:
        """Reads the Markdown content of a page. `name` without the .md extension."""
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(f"{base}/.fs/{_page_path(name)}", headers=headers)
            if r.status_code == 404:
                return f"Page not found: {name}"
            r.raise_for_status()
            return r.text

    @mcp.tool()
    async def search_pages(query: str, max_hits: int = 20) -> str:
        """Case-insensitive search in page names and bodies."""
        needle = query.lower()
        files = await _list_md()
        hits: list[str] = []
        sem = asyncio.Semaphore(8)

        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            async def probe(f: dict) -> None:
                page = f["name"][:-3]
                if needle in page.lower():
                    hits.append(page)
                    return
                async with sem:
                    r = await c.get(f"{base}/.fs/{f['name']}", headers=headers)
                if r.status_code == 200 and needle in r.text.lower():
                    hits.append(page)

            await asyncio.gather(*(probe(f) for f in files), return_exceptions=True)

        if not hits:
            return f"No result for: {query}"
        return "\n".join(sorted(hits)[:max_hits])

    @mcp.tool()
    async def create_note(name: str, content: str, destination: str = "") -> str:
        """Creates a new note. Fails if it already exists. The name must start with
        the allowed write prefix.

        `destination` is the page where the note should eventually land, if it is not
        the one being written. The server then adds the frontmatter and the *File*
        button: the user clicks, the body goes to the destination and the note is
        deleted. `content` stays the body alone, without frontmatter."""
        try:
            path = _writable(name, write_prefix)
        except ValueError as e:
            return str(e)
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.put(
                f"{base}/.fs/{path}",
                headers={
                    **headers,
                    "Content-Type": "text/markdown",
                    "If-None-Match": "*",
                },
                content=_render(content, destination).encode("utf-8"),
            )
        if r.status_code == 412:
            return f"Already exists, nothing written: {name}"
        r.raise_for_status()
        return f"Created: {name}"

    @mcp.tool()
    async def append_to_note(name: str, text: str) -> str:
        """Appends text at the end of an existing note, without overwriting the rest."""
        try:
            path = _writable(name, write_prefix)
        except ValueError as e:
            return str(e)
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(f"{base}/.fs/{path}", headers=headers)
            if r.status_code == 404:
                return f"Page not found: {name}"
            r.raise_for_status()
            etag = r.headers.get("ETag")
            body = r.text.rstrip("\n") + "\n" + text.strip() + "\n"
            put_headers = {**headers, "Content-Type": "text/markdown"}
            if etag:
                put_headers["If-Match"] = etag
            w = await c.put(
                f"{base}/.fs/{path}",
                headers=put_headers,
                content=body.encode("utf-8"),
            )
        if w.status_code == 412:
            return "Changed in the meantime, nothing written. Read the page again and retry."
        w.raise_for_status()
        return f"Appended to: {name}"

    @mcp.tool()
    async def replace_note(name: str, content: str, destination: str = "") -> str:
        """Replaces the whole content of an existing note. Useful to correct a note
        just written; `destination` has the same meaning as in create_note."""
        try:
            path = _writable(name, write_prefix)
        except ValueError as e:
            return str(e)
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(f"{base}/.fs/{path}", headers=headers)
            if r.status_code == 404:
                return f"Page not found: {name}"
            r.raise_for_status()
            etag = r.headers.get("ETag")
            put_headers = {**headers, "Content-Type": "text/markdown"}
            if etag:
                put_headers["If-Match"] = etag
            w = await c.put(
                f"{base}/.fs/{path}",
                headers=put_headers,
                content=_render(content, destination).encode("utf-8"),
            )
        if w.status_code == 412:
            return "Changed in the meantime, nothing written. Read the page again and retry."
        w.raise_for_status()
        return f"Replaced: {name}"

    @mcp.tool()
    async def delete_note(name: str) -> str:
        """Deletes a note. Irreversible, and limited to the write prefix."""
        try:
            path = _writable(name, write_prefix)
        except ValueError as e:
            return str(e)
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.delete(f"{base}/.fs/{path}", headers=headers)
        if r.status_code == 404:
            return f"Page not found: {name}"
        r.raise_for_status()
        return f"Deleted: {name}"

    return mcp


if __name__ == "__main__":
    config = Config.from_toml(sys.argv[1] if len(sys.argv) > 1 else "config.toml")
    build_server(config).run(
        transport="streamable-http", host=config.host, port=config.port
    )
