"""MCP server exposing a remote SilverBullet space, over streamable-http.

The server has no authentication of its own: it listens on the loopback
interface and must stay behind a reverse proxy handling TLS and auth (see
Caddyfile.example). Do not set HOST to 0.0.0.0 without such a proxy.

Environment variables:
  SB_BASE_URL  space URL, prefix included (e.g. https://notes.example.fr/work)
  SB_TOKEN     account API token (admin UI > Users > API tokens)
  SB_WRITE_PREFIX  prefix under which writing is allowed (default: "Inbox/")
  SB_HIDE_PREFIXES  prefixes excluded from listings and searches, separated by
                    commas (default: "Library/")
  HOST         listening interface (default: 127.0.0.1)
  PORT         listening port (default: 8000)
"""

import os
import asyncio
import httpx
from mcp.server.mcpserver import MCPServer

BASE = os.environ["SB_BASE_URL"].rstrip("/")
TOKEN = os.environ["SB_TOKEN"]
WRITE_PREFIX = os.environ.get("SB_WRITE_PREFIX", "Inbox/")
HIDE_PREFIXES = tuple(
    p.strip()
    for p in os.environ.get("SB_HIDE_PREFIXES", "Library/").split(",")
    if p.strip()
)

HEADERS = {"Authorization": f"Bearer {TOKEN}"}
TIMEOUT = httpx.Timeout(30.0)

INSTRUCTIONS = f"""Access to SilverBullet notes.

Reading covers the whole space, writing only under {WRITE_PREFIX}.

A note meant for another page is still created with create_note, by filling in
`destination` (e.g. "Journal/2026-09-10"): it is dropped under {WRITE_PREFIX}
with a *File* button the user clicks in SilverBullet to append the body to the
destination and delete the note. Never write that frontmatter by hand, the
server takes care of it."""

mcp = MCPServer("silverbullet", instructions=INSTRUCTIONS)


def _page_path(name: str) -> str:
    name = name.strip().strip("/")
    if name.endswith(".md"):
        name = name[:-3]
    if ".." in name:
        raise ValueError("invalid page name")
    return f"{name}.md"


async def _list_md() -> list[dict]:
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.get(f"{BASE}/.fs", headers=HEADERS)
        r.raise_for_status()
        return [
            f
            for f in r.json()
            if f["name"].endswith(".md")
            and not f["name"].startswith(HIDE_PREFIXES)
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
        r = await c.get(f"{BASE}/.fs/{_page_path(name)}", headers=HEADERS)
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
                r = await c.get(f"{BASE}/.fs/{f['name']}", headers=HEADERS)
            if r.status_code == 200 and needle in r.text.lower():
                hits.append(page)

        await asyncio.gather(*(probe(f) for f in files), return_exceptions=True)

    if not hits:
        return f"No result for: {query}"
    return "\n".join(sorted(hits)[:max_hits])


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


def _writable(name: str) -> str:
    path = _page_path(name)
    if not path.startswith(WRITE_PREFIX):
        raise ValueError(
            f"writing allowed only under {WRITE_PREFIX} (got: {path})"
        )
    return path


@mcp.tool()
async def create_note(name: str, content: str, destination: str = "") -> str:
    """Creates a new note. Fails if it already exists. The name must start with
    the allowed write prefix.

    `destination` is the page where the note should eventually land, if it is not
    the one being written. The server then adds the frontmatter and the *File*
    button: the user clicks, the body goes to the destination and the note is
    deleted. `content` stays the body alone, without frontmatter."""
    try:
        path = _writable(name)
    except ValueError as e:
        return str(e)
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.put(
            f"{BASE}/.fs/{path}",
            headers={**HEADERS, "Content-Type": "text/markdown", "If-None-Match": "*"},
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
        path = _writable(name)
    except ValueError as e:
        return str(e)
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.get(f"{BASE}/.fs/{path}", headers=HEADERS)
        if r.status_code == 404:
            return f"Page not found: {name}"
        r.raise_for_status()
        etag = r.headers.get("ETag")
        body = r.text.rstrip("\n") + "\n" + text.strip() + "\n"
        put_headers = {**HEADERS, "Content-Type": "text/markdown"}
        if etag:
            put_headers["If-Match"] = etag
        w = await c.put(f"{BASE}/.fs/{path}", headers=put_headers, content=body.encode("utf-8"))
    if w.status_code == 412:
        return "Changed in the meantime, nothing written. Read the page again and retry."
    w.raise_for_status()
    return f"Appended to: {name}"


@mcp.tool()
async def replace_note(name: str, content: str, destination: str = "") -> str:
    """Replaces the whole content of an existing note. Useful to correct a note
    just written; `destination` has the same meaning as in create_note."""
    try:
        path = _writable(name)
    except ValueError as e:
        return str(e)
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.get(f"{BASE}/.fs/{path}", headers=HEADERS)
        if r.status_code == 404:
            return f"Page not found: {name}"
        r.raise_for_status()
        etag = r.headers.get("ETag")
        put_headers = {**HEADERS, "Content-Type": "text/markdown"}
        if etag:
            put_headers["If-Match"] = etag
        w = await c.put(
            f"{BASE}/.fs/{path}",
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
        path = _writable(name)
    except ValueError as e:
        return str(e)
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.delete(f"{BASE}/.fs/{path}", headers=HEADERS)
    if r.status_code == 404:
        return f"Page not found: {name}"
    r.raise_for_status()
    return f"Deleted: {name}"


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
    )
