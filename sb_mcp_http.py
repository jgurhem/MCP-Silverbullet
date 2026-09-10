"""Serveur MCP exposant un espace SilverBullet distant, en streamable-http.

Le serveur n'a pas d'authentification propre: il ecoute sur la loopback et
doit rester derriere un reverse proxy qui gere le TLS et l'auth (voir
Caddyfile.example). Ne pas mettre HOST a 0.0.0.0 sans un tel proxy.

Variables d'environnement:
  SB_BASE_URL  URL de l'espace, prefixe inclus (ex: https://notes.example.fr/work)
  SB_TOKEN     token d'API du compte (admin UI > Users > API tokens)
  SB_WRITE_PREFIX  prefixe sous lequel l'ecriture est autorisee (defaut: "Inbox/")
  SB_HIDE_PREFIXES  prefixes exclus des listes et recherches, separes par des
                    virgules (defaut: "Library/")
  HOST         interface d'ecoute (defaut: 127.0.0.1)
  PORT         port d'ecoute (defaut: 8000)
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

mcp = MCPServer("silverbullet", instructions="Acces aux notes SilverBullet.")


def _page_path(name: str) -> str:
    name = name.strip().strip("/")
    if name.endswith(".md"):
        name = name[:-3]
    if ".." in name:
        raise ValueError("nom de page invalide")
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
    """Liste les pages de l'espace, les plus recemment modifiees d'abord.
    Les pages systeme (Library/ par defaut) sont exclues."""
    files = await _list_md()
    files.sort(key=lambda f: f.get("lastModified", 0), reverse=True)
    return "\n".join(f["name"][:-3] for f in files) or "(espace vide)"


@mcp.tool()
async def read_page(name: str) -> str:
    """Lit le contenu Markdown d'une page. `name` sans l'extension .md."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.get(f"{BASE}/.fs/{_page_path(name)}", headers=HEADERS)
        if r.status_code == 404:
            return f"Page introuvable: {name}"
        r.raise_for_status()
        return r.text


@mcp.tool()
async def search_pages(query: str, max_hits: int = 20) -> str:
    """Recherche insensible a la casse dans le nom et le corps des pages."""
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
        return f"Aucun resultat pour: {query}"
    return "\n".join(sorted(hits)[:max_hits])


def _writable(name: str) -> str:
    path = _page_path(name)
    if not path.startswith(WRITE_PREFIX):
        raise ValueError(
            f"ecriture autorisee uniquement sous {WRITE_PREFIX} (recu: {path})"
        )
    return path


@mcp.tool()
async def create_note(name: str, content: str) -> str:
    """Cree une nouvelle note. Echoue si elle existe deja. Le nom doit commencer
    par le prefixe d'ecriture autorise."""
    path = _writable(name)
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.put(
            f"{BASE}/.fs/{path}",
            headers={**HEADERS, "Content-Type": "text/markdown", "If-None-Match": "*"},
            content=content.encode("utf-8"),
        )
    if r.status_code == 412:
        return f"Existe deja, rien ecrit: {name}"
    r.raise_for_status()
    return f"Cree: {name}"


@mcp.tool()
async def append_to_note(name: str, text: str) -> str:
    """Ajoute du texte a la fin d'une note existante, sans ecraser le reste."""
    path = _writable(name)
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.get(f"{BASE}/.fs/{path}", headers=HEADERS)
        if r.status_code == 404:
            return f"Page introuvable: {name}"
        r.raise_for_status()
        etag = r.headers.get("ETag")
        body = r.text.rstrip("\n") + "\n" + text.strip() + "\n"
        put_headers = {**HEADERS, "Content-Type": "text/markdown"}
        if etag:
            put_headers["If-Match"] = etag
        w = await c.put(f"{BASE}/.fs/{path}", headers=put_headers, content=body.encode("utf-8"))
    if w.status_code == 412:
        return "Modifiee entre-temps, rien ecrit. Relis la page et reessaie."
    w.raise_for_status()
    return f"Ajoute a: {name}"


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
    )
