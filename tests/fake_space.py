"""A minimal stand-in for SilverBullet's `/.fs` HTTP API.

It is an ASGI app rather than a set of canned responses so that the
conditional requests the server relies on — `If-None-Match: *` on create,
`If-Match: <etag>` on append and replace — are really exercised: the 412s
come from comparing stored state, not from a stub told to return one.
"""

import json

MOUNT = "/work"


class FakeSpace:
    def __init__(self, files: dict[str, str] | None = None):
        # name -> {"content", "lastModified", "etag"}
        self.files: dict[str, dict] = {}
        self.requests: list[dict] = []
        # Called with a page name right after its content is served, to stage a
        # change between a tool's GET and its PUT.
        self.on_get = None
        self._clock = 0
        for name, content in (files or {}).items():
            self.write(name, content)

    def write(self, name: str, content: str) -> None:
        self._clock += 1
        self.files[name] = {
            "content": content,
            "lastModified": self._clock,
            "etag": f'"{self._clock}"',
        }

    def content(self, name: str) -> str:
        return self.files[name]["content"]

    def gets(self, path: str) -> int:
        """How many times a given path was fetched."""
        return sum(
            1 for r in self.requests if r["method"] == "GET" and r["path"] == path
        )

    async def app(self, scope, receive, send):
        assert scope["type"] == "http"
        headers = {
            k.decode().lower(): v.decode() for k, v in scope["headers"]
        }
        method, path = scope["method"], scope["path"]
        self.requests.append({"method": method, "path": path, "headers": headers})

        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break

        status, out, extra = self._route(method, path, headers, body)
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (k.encode(), v.encode())
                    for k, v in {"content-type": "text/plain", **extra}.items()
                ],
            }
        )
        await send({"type": "http.response.body", "body": out})

    def _route(self, method, path, headers, body):
        if not path.startswith(MOUNT + "/"):
            return 404, b"no such space", {}
        rest = path[len(MOUNT):]

        if rest == "/.fs" and method == "GET":
            listing = [
                {"name": name, "lastModified": meta["lastModified"]}
                for name, meta in self.files.items()
            ]
            return (
                200,
                json.dumps(listing).encode(),
                {"content-type": "application/json"},
            )

        if not rest.startswith("/.fs/"):
            return 404, b"not found", {}
        name = rest[len("/.fs/"):]
        entry = self.files.get(name)

        if method == "GET":
            if entry is None:
                return 404, b"not found", {}
            response = (
                200,
                entry["content"].encode(),
                {"content-type": "text/markdown", "etag": entry["etag"]},
            )
            if self.on_get is not None:
                self.on_get(name)
            return response

        if method == "PUT":
            if headers.get("if-none-match") == "*" and entry is not None:
                return 412, b"exists", {}
            if_match = headers.get("if-match")
            if if_match is not None and (entry is None or entry["etag"] != if_match):
                return 412, b"stale", {}
            self.write(name, body.decode())
            return 200, b"ok", {"etag": self.files[name]["etag"]}

        if method == "DELETE":
            if entry is None:
                return 404, b"not found", {}
            if_match = headers.get("if-match")
            if if_match is not None and entry["etag"] != if_match:
                return 412, b"stale", {}
            del self.files[name]
            return 200, b"ok", {}

        return 405, b"method not allowed", {}
