import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.mcpserver.exceptions import ToolError

import sb_mcp_http
from sb_mcp_http import Config, _page_path, _render, _writable, build_server
from fake_space import MOUNT, FakeSpace

BASE = f"http://space.test{MOUNT}"
TOKEN = "tok"


@pytest.fixture
def space(monkeypatch):
    """A fake space, with every httpx client in the server routed to it."""
    sp = FakeSpace(
        {
            "Inbox/note.md": "a note\n",
            "Journal/2026-09-10.md": "the day\n",
            "Library/std/Core.md": "system stuff\n",
            "assets/logo.png": "not markdown",
        }
    )
    real_client = httpx.AsyncClient

    def factory(**kwargs):
        return real_client(transport=httpx.ASGITransport(app=sp.app), **kwargs)

    monkeypatch.setattr(sb_mcp_http.httpx, "AsyncClient", factory)
    return sp


def make_server(**overrides):
    return build_server(Config(base_url=BASE, token=TOKEN, **overrides))


async def call(server, tool, **args):
    result = await server.call_tool(tool, args)
    return result.content[0].text


# --- configuration ---------------------------------------------------------


def write_toml(tmp_path, text):
    path = tmp_path / "config.toml"
    path.write_text(text)
    return str(path)


def test_config_from_toml_minimal(tmp_path):
    path = write_toml(tmp_path, 'base_url = "http://x/work"\ntoken = "t"\n')
    config = Config.from_toml(path)
    assert config.base_url == "http://x/work"
    assert config.token == "t"
    assert config.write_prefix == "Inbox/"
    assert config.hide_prefixes == ("Library/",)
    assert (config.host, config.port) == ("127.0.0.1", 8000)


def test_config_from_toml_full(tmp_path):
    path = write_toml(
        tmp_path,
        """
        base_url = "http://x/work/"
        token = "t"
        write_prefix = "Drafts/"
        hide_prefixes = ["Library/", "Meta/"]
        host = "0.0.0.0"
        port = 9001
        """,
    )
    config = Config.from_toml(path)
    assert config.base_url == "http://x/work"  # trailing slash normalised away
    assert config.write_prefix == "Drafts/"
    assert config.hide_prefixes == ("Library/", "Meta/")
    assert (config.host, config.port) == ("0.0.0.0", 9001)


def test_config_rejects_missing_required_key(tmp_path):
    path = write_toml(tmp_path, 'base_url = "http://x/work"\n')
    with pytest.raises(ValueError, match="missing key.*token"):
        Config.from_toml(path)


def test_config_rejects_unknown_key(tmp_path):
    # A typo must not fall back to a default: `hide-prefixes` silently ignored
    # would expose the pages it was meant to hide.
    path = write_toml(
        tmp_path,
        'base_url = "http://x/work"\ntoken = "t"\nhide-prefixes = ["Library/"]\n',
    )
    with pytest.raises(ValueError, match="unknown key"):
        Config.from_toml(path)


# --- page names ------------------------------------------------------------


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Inbox/note", "Inbox/note.md"),
        ("Inbox/note.md", "Inbox/note.md"),
        ("  Inbox/note  ", "Inbox/note.md"),
        ("/Inbox/note/", "Inbox/note.md"),
        ("Journal/2026-09-10", "Journal/2026-09-10.md"),
    ],
)
def test_page_path(name, expected):
    assert _page_path(name) == expected


@pytest.mark.parametrize("name", ["../secrets", "Inbox/../Journal/x", ".."])
def test_page_path_rejects_traversal(name):
    with pytest.raises(ValueError):
        _page_path(name)


def test_page_path_rejects_double_dot_anywhere():
    # Documents current behaviour: the check is a plain substring test, so a
    # legitimate name containing ".." is refused too.
    with pytest.raises(ValueError):
        _page_path("Inbox/really..important")


def test_writable_accepts_prefix():
    assert _writable("Inbox/note", "Inbox/") == "Inbox/note.md"


@pytest.mark.parametrize("name", ["Journal/2026-09-10", "Inboxes/note", "Inbox"])
def test_writable_refuses_outside_prefix(name):
    with pytest.raises(ValueError, match="writing allowed only under Inbox/"):
        _writable(name, "Inbox/")


def test_writable_follows_configured_prefix():
    assert _writable("Drafts/x", "Drafts/") == "Drafts/x.md"
    with pytest.raises(ValueError):
        _writable("Inbox/x", "Drafts/")


# --- note rendering --------------------------------------------------------


def test_render_without_destination_passes_content_through():
    assert _render("  raw body  ", "") == "  raw body  "
    assert _render("raw body", "   ") == "raw body"


def test_render_with_destination_adds_frontmatter_and_button():
    assert _render("  hello  ", " Journal/2026-09-10 ") == (
        "---\ndestination: Journal/2026-09-10\n---\n"
        "${inbox.button()}\n\nhello\n"
    )


# --- list_pages ------------------------------------------------------------


async def test_list_pages_orders_by_last_modified_and_hides_prefixes(space):
    space.write("Inbox/fresh.md", "newest")
    out = await call(make_server(), "list_pages")
    assert out.splitlines() == ["Inbox/fresh", "Journal/2026-09-10", "Inbox/note"]


async def test_list_pages_honours_configured_hide_prefixes(space):
    out = await call(make_server(hide_prefixes=("Library/", "Journal/")), "list_pages")
    assert "Journal/2026-09-10" not in out
    assert "Inbox/note" in out


async def test_list_pages_without_hide_prefixes_hides_nothing(space):
    out = await call(make_server(hide_prefixes=()), "list_pages")
    assert "Library/std/Core" in out


async def test_list_pages_on_empty_space(space):
    space.files.clear()
    assert await call(make_server(), "list_pages") == "(empty space)"


async def test_requests_carry_the_token(space):
    await call(make_server(), "list_pages")
    assert space.requests[0]["headers"]["authorization"] == f"Bearer {TOKEN}"


# --- read_page -------------------------------------------------------------


async def test_read_page(space):
    assert await call(make_server(), "read_page", name="Inbox/note") == "a note\n"


async def test_read_page_reads_outside_the_write_prefix(space):
    assert await call(make_server(), "read_page", name="Library/std/Core") == (
        "system stuff\n"
    )


async def test_read_page_missing(space):
    out = await call(make_server(), "read_page", name="Inbox/ghost")
    assert out == "Page not found: Inbox/ghost"


# --- search_pages ----------------------------------------------------------


async def test_search_matches_page_name_without_fetching_the_body(space):
    out = await call(make_server(), "search_pages", query="journal")
    assert out == "Journal/2026-09-10"
    assert space.gets(f"{MOUNT}/.fs/Journal/2026-09-10.md") == 0


async def test_search_matches_body_case_insensitively(space):
    space.write("Inbox/other.md", "Contains A NOTE inside\n")
    out = await call(make_server(), "search_pages", query="a note")
    assert out.splitlines() == ["Inbox/note", "Inbox/other"]


async def test_search_skips_hidden_pages(space):
    out = await call(make_server(), "search_pages", query="system stuff")
    assert out == "No result for: system stuff"


async def test_search_caps_the_number_of_hits(space):
    for i in range(5):
        space.write(f"Inbox/hit-{i}.md", "needle\n")
    out = await call(make_server(), "search_pages", query="needle", max_hits=3)
    assert out.splitlines() == ["Inbox/hit-0", "Inbox/hit-1", "Inbox/hit-2"]


# --- create_note -----------------------------------------------------------


async def test_create_note(space):
    out = await call(make_server(), "create_note", name="Inbox/new", content="body")
    assert out == "Created: Inbox/new"
    assert space.content("Inbox/new.md") == "body"


async def test_create_note_with_destination(space):
    await call(
        make_server(),
        "create_note",
        name="Inbox/new",
        content="2:30pm client sync",
        destination="Journal/2026-09-10",
    )
    assert space.content("Inbox/new.md") == (
        "---\ndestination: Journal/2026-09-10\n---\n"
        "${inbox.button()}\n\n2:30pm client sync\n"
    )


async def test_create_note_refuses_to_overwrite(space):
    out = await call(make_server(), "create_note", name="Inbox/note", content="new")
    assert out == "Already exists, nothing written: Inbox/note"
    assert space.content("Inbox/note.md") == "a note\n"


async def test_create_note_outside_prefix_never_reaches_the_space(space):
    out = await call(
        make_server(), "create_note", name="Journal/2026-09-10", content="x"
    )
    assert out.startswith("writing allowed only under Inbox/")
    assert space.content("Journal/2026-09-10.md") == "the day\n"
    assert space.requests == []


# --- append_to_note --------------------------------------------------------


async def test_append_to_note(space):
    out = await call(make_server(), "append_to_note", name="Inbox/note", text="  more  ")
    assert out == "Appended to: Inbox/note"
    assert space.content("Inbox/note.md") == "a note\nmore\n"


async def test_append_sends_the_etag_it_read(space):
    etag = space.files["Inbox/note.md"]["etag"]
    await call(make_server(), "append_to_note", name="Inbox/note", text="more")
    put = [r for r in space.requests if r["method"] == "PUT"][-1]
    assert put["headers"]["if-match"] == etag


async def test_append_refuses_a_concurrent_change(space):
    # Someone else writes between our GET and our PUT: the ETag we read is
    # stale, and the write must be refused rather than clobber their change.
    space.on_get = lambda name: space.write(name, "changed elsewhere\n")
    out = await call(make_server(), "append_to_note", name="Inbox/note", text="more")
    assert out.startswith("Changed in the meantime")
    assert space.content("Inbox/note.md") == "changed elsewhere\n"


async def test_replace_refuses_a_concurrent_change(space):
    space.on_get = lambda name: space.write(name, "changed elsewhere\n")
    out = await call(make_server(), "replace_note", name="Inbox/note", content="mine")
    assert out.startswith("Changed in the meantime")
    assert space.content("Inbox/note.md") == "changed elsewhere\n"


async def test_append_to_missing_note(space):
    out = await call(make_server(), "append_to_note", name="Inbox/ghost", text="x")
    assert out == "Page not found: Inbox/ghost"


async def test_append_outside_prefix(space):
    out = await call(
        make_server(), "append_to_note", name="Journal/2026-09-10", text="x"
    )
    assert out.startswith("writing allowed only under Inbox/")
    assert space.requests == []


# --- replace_note ----------------------------------------------------------


async def test_replace_note(space):
    out = await call(
        make_server(), "replace_note", name="Inbox/note", content="rewritten\n"
    )
    assert out == "Replaced: Inbox/note"
    assert space.content("Inbox/note.md") == "rewritten\n"


async def test_replace_note_with_destination(space):
    await call(
        make_server(),
        "replace_note",
        name="Inbox/note",
        content="fixed",
        destination="Journal/2026-09-10",
    )
    assert space.content("Inbox/note.md").startswith(
        "---\ndestination: Journal/2026-09-10\n---\n"
    )


async def test_replace_missing_note(space):
    out = await call(make_server(), "replace_note", name="Inbox/ghost", content="x")
    assert out == "Page not found: Inbox/ghost"


async def test_replace_outside_prefix(space):
    out = await call(
        make_server(), "replace_note", name="Journal/2026-09-10", content="x"
    )
    assert out.startswith("writing allowed only under Inbox/")
    assert space.requests == []


# --- delete_note -----------------------------------------------------------


async def test_delete_note(space):
    out = await call(make_server(), "delete_note", name="Inbox/note")
    assert out == "Deleted: Inbox/note"
    assert "Inbox/note.md" not in space.files


async def test_delete_missing_note(space):
    out = await call(make_server(), "delete_note", name="Inbox/ghost")
    assert out == "Page not found: Inbox/ghost"


async def test_delete_outside_prefix(space):
    out = await call(make_server(), "delete_note", name="Journal/2026-09-10")
    assert out.startswith("writing allowed only under Inbox/")
    assert "Journal/2026-09-10.md" in space.files
    assert space.requests == []


# --- MCP surface -----------------------------------------------------------


async def test_all_tools_are_exposed():
    tools = await make_server().list_tools()
    assert {t.name for t in tools} == {
        "list_pages",
        "read_page",
        "search_pages",
        "create_note",
        "append_to_note",
        "replace_note",
        "delete_note",
    }


async def test_create_note_schema_makes_destination_optional():
    tools = {t.name: t for t in await make_server().list_tools()}
    schema = tools["create_note"].input_schema
    assert set(schema["required"]) == {"name", "content"}
    assert schema["properties"]["destination"]["default"] == ""


def test_instructions_mention_the_configured_write_prefix():
    assert "Drafts/" in make_server(write_prefix="Drafts/").instructions


# --- page names needing URL quoting ----------------------------------------

SPECIAL_NAMES = ["Projets/Q&A #1", "Notes/what?", "Journal/100% done", "Inbox/été"]


@pytest.mark.parametrize("name", SPECIAL_NAMES)
async def test_read_page_quotes_special_characters(space, name):
    # A `#` or `?` left raw in the URL cuts the path short, and the request
    # lands on another page — or on none.
    space.write(f"{name}.md", "the body\n")
    assert await call(make_server(), "read_page", name=name) == "the body\n"


@pytest.mark.parametrize("name", ["Inbox/Q&A #1", "Inbox/what?", "Inbox/100% done"])
async def test_write_tools_quote_special_characters(space, name):
    server = make_server()
    assert await call(server, "create_note", name=name, content="first") == (
        f"Created: {name}"
    )
    assert space.content(f"{name}.md") == "first"

    assert await call(server, "append_to_note", name=name, text="second") == (
        f"Appended to: {name}"
    )
    assert space.content(f"{name}.md") == "first\nsecond\n"

    assert await call(server, "replace_note", name=name, content="third") == (
        f"Replaced: {name}"
    )
    assert space.content(f"{name}.md") == "third"

    assert await call(server, "delete_note", name=name) == f"Deleted: {name}"
    assert f"{name}.md" not in space.files


async def test_search_reaches_bodies_of_special_names(space):
    space.write("Projets/Q&A #1.md", "the needle is here\n")
    out = await call(make_server(), "search_pages", query="the needle")
    assert out == "Projets/Q&A #1"


async def test_read_page_decodes_utf8_without_charset_header(space):
    # The fake space serves `text/markdown` with no charset, as SilverBullet
    # does: guard against a silent mojibake regression.
    space.write("Inbox/accents.md", "réunion avec Benoît — café\n")
    assert await call(make_server(), "read_page", name="Inbox/accents") == (
        "réunion avec Benoît — café\n"
    )


# --- backend failures stay on the server -----------------------------------


@pytest.fixture
def broken_space(monkeypatch):
    """Routes every request to a handler of the test's choosing."""

    def install(handler):
        real_client = httpx.AsyncClient

        def factory(**kwargs):
            return real_client(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(sb_mcp_http.httpx, "AsyncClient", factory)

    return install


def _refused(request):
    return httpx.Response(401, text=f"bad bearer token {TOKEN}")


def _unreachable(request):
    raise httpx.ConnectError("failed to connect to silverbullet:3000")


async def client_text(server, tool, args):
    """The text a client would end up seeing, whether the tool returned a
    string or raised: server.py turns a ToolError into the reply body."""
    try:
        result = await server.call_tool(tool, args)
    except ToolError as exc:
        return str(exc)
    return result.content[0].text


@pytest.mark.parametrize("handler", [_refused, _unreachable])
@pytest.mark.parametrize(
    "tool, args",
    [
        ("list_pages", {}),
        ("read_page", {"name": "Journal/2026-09-11"}),
        ("search_pages", {"query": "anything"}),
        ("create_note", {"name": "Inbox/note", "content": "x"}),
        ("append_to_note", {"name": "Inbox/note", "text": "x"}),
        ("delete_note", {"name": "Inbox/note"}),
    ],
)
async def test_backend_failure_leaks_nothing_to_the_client(
    broken_space, handler, tool, args
):
    # What the SilverBullet leg reveals — the token, the internal URL, the
    # status — belongs in the server log, not in the client's reply. The
    # detail survives on the exception's __cause__, which never leaves here.
    broken_space(handler)
    message = await client_text(make_server(), tool, args)
    for secret in (TOKEN, BASE, "space.test", "401", "bearer", "silverbullet"):
        assert secret.lower() not in message.lower(), f"{secret!r} leaked: {message!r}"


@pytest.mark.parametrize("handler", [_refused, _unreachable])
async def test_backend_failure_detail_is_kept_for_the_log(broken_space, handler):
    # The generic message is only safe because the cause is still attached:
    # server.py logs it with logger.exception.
    broken_space(handler)
    with pytest.raises(ToolError) as excinfo:
        await make_server().call_tool("list_pages", {})
    assert excinfo.value.__cause__ is not None
