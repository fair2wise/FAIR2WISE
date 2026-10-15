"""A strict, executable model of the subset of nginx that ``docker/nginx.conf`` uses (T24C).

No nginx binary is available to the test suite (and no image may be built), so this module
parses the real gateway file and evaluates requests with nginx's documented semantics:

* location selection (http://nginx.org/en/docs/http/ngx_http_core_module.html#location):
  ``=`` exact first, then the longest prefix; a ``^~`` longest prefix stops the search,
  otherwise regex locations are tried in file order and the first match wins, else the
  longest prefix. Matching uses the *normalized* URI (decoded, merged slashes, dot
  segments resolved);
* ``map`` (exact strings before regexes in order, named captures usable in the value),
  ``if ($v = "")`` / ``!=`` with ``return``, ``return``, ``try_files`` (file, ``dir/`` and
  ``=code`` / internal-redirect fallbacks), the static module's directory 301 and ``index``;
* ``proxy_pass``: with variables the evaluated URI is sent verbatim; with a static URI
  part the matched location prefix of the normalized URI is replaced; without a URI the
  raw request URI is sent; ``proxy_set_header`` inheritance (only when a level defines
  none) and an empty value dropping the header.

Any directive this model does not know raises :class:`UnsupportedDirective`, so the
configuration cannot drift outside what the tests actually execute.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import unquote


class UnsupportedDirective(AssertionError):
    pass


def pcre(pattern: str) -> "re.Pattern[str]":
    """Compile an nginx (PCRE) regex: ``(?<name>...)`` named groups become Python's ``(?P<name>...)``."""
    flags = re.I if pattern.startswith("~*") else 0
    body = pattern[2:] if pattern.startswith("~*") else pattern.lstrip("~")
    return re.compile(re.sub(r"\(\?<(?=[A-Za-z_])", "(?P<", body), flags)


@dataclass
class Directive:
    name: str
    args: List[str]
    block: Optional[List["Directive"]] = None


def tokenize(text: str) -> List[str]:
    tokens: List[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c == "#":
            while i < n and text[i] != "\n":
                i += 1
        elif c in "{};":
            tokens.append(c)
            i += 1
        elif c in "\"'":
            quote, j, buf = c, i + 1, []
            while text[j] != quote:
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j:j + 2] if text[j + 1] not in "\"'" else text[j + 1])
                    j += 2
                    continue
                buf.append(text[j])
                j += 1
            tokens.append("\x00" + "".join(buf))  # \x00 marks a quoted (possibly empty) token
            i = j + 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in "{};":
                j += 1
            tokens.append(text[i:j])
            i = j
    return tokens


def parse(text: str) -> List[Directive]:
    tokens = tokenize(text)
    pos = 0

    def block() -> List[Directive]:
        nonlocal pos
        out: List[Directive] = []
        while pos < len(tokens) and tokens[pos] != "}":
            words: List[str] = []
            while tokens[pos] not in ("{", ";"):
                words.append(tokens[pos].lstrip("\x00"))
                pos += 1
            if tokens[pos] == ";":
                pos += 1
                out.append(Directive(words[0], words[1:]))
            else:
                pos += 1
                inner = block()
                assert tokens[pos] == "}", "unbalanced braces"
                pos += 1
                out.append(Directive(words[0], words[1:], inner))
        return out

    tree = block()
    assert pos == len(tokens), "trailing tokens"
    return tree


def normalize_uri(raw_path: str) -> Optional[str]:
    """nginx URI normalization; ``None`` for a path that climbs above the root (400)."""
    decoded = unquote(raw_path)
    merged = re.sub(r"/{2,}", "/", decoded)
    out: List[str] = []
    parts = merged.split("/")
    for idx, part in enumerate(parts[1:], start=1):
        last = idx == len(parts) - 1
        if part == ".":
            if last:
                out.append("")
            continue
        if part == "..":
            if not out:
                return None
            out.pop()
            if last:
                out.append("")
            continue
        out.append(part)
    return "/" + "/".join(out)


@dataclass
class Request:
    method: str
    raw_uri: str  # path + optional ?query, exactly as on the request line
    headers: Dict[str, str] = field(default_factory=dict)  # lower-case names
    scheme: str = "http"
    remote_addr: str = "203.0.113.7"

    @property
    def raw_path(self) -> str:
        return self.raw_uri.split("?", 1)[0]

    @property
    def args(self) -> str:
        return self.raw_uri.split("?", 1)[1] if "?" in self.raw_uri else ""


@dataclass
class Result:
    kind: str  # "return" | "static" | "proxy"
    status: int = 200
    body: str = ""
    location: Optional[str] = None
    file: Optional[Path] = None
    headers: Dict[str, str] = field(default_factory=dict)  # response add_header
    location_block: Optional["Location"] = None
    upstream: Optional[str] = None
    proxy_uri: Optional[str] = None
    proxy_headers: Dict[str, str] = field(default_factory=dict)
    trace: List[str] = field(default_factory=list)


@dataclass
class Location:
    modifier: str  # "=", "^~", "", "~", "~*"
    pattern: str
    directives: List[Directive]

    def directive(self, name: str) -> List[Directive]:
        return [d for d in self.directives if d.name == name]


KNOWN_LOCATION_DIRECTIVES = {
    "if", "return", "proxy_pass", "proxy_http_version", "proxy_set_header", "proxy_redirect",
    "proxy_buffering", "proxy_cache", "proxy_request_buffering", "proxy_read_timeout",
    "proxy_send_timeout", "gzip", "client_max_body_size", "try_files", "index", "expires",
    "add_header", "access_log", "default_type",
}
KNOWN_SERVER_DIRECTIVES = {"listen", "server_name", "root", "index", "absolute_redirect", "server_tokens",
                           "add_header", "location"}
REDIRECT_CODES = {301, 302, 303, 307, 308}


class NginxModel:
    """Evaluate requests against one parsed ``server`` block."""

    def __init__(self, text: str, *, root: Optional[Path] = None) -> None:
        tree = parse(text)
        self.upstreams: Dict[str, List[str]] = {}
        self.maps: Dict[str, Tuple[str, List[Tuple[str, str]]]] = {}
        servers = []
        for d in tree:
            if d.name == "upstream":
                self.upstreams[d.args[0]] = [x.args[0] for x in d.block if x.name == "server"]
                unknown = {x.name for x in d.block} - {"server", "keepalive"}
                if unknown:
                    raise UnsupportedDirective(f"upstream: {unknown}")
            elif d.name == "map":
                source, target = d.args
                self.maps[target.lstrip("$")] = (source, [(x.name, x.args[0] if x.args else "") for x in d.block])
            elif d.name == "server":
                servers.append(d)
            else:
                raise UnsupportedDirective(f"http-level {d.name}")
        assert len(servers) == 1, "exactly one server block"
        self.server = servers[0].block
        for d in self.server:
            if d.name not in KNOWN_SERVER_DIRECTIVES:
                raise UnsupportedDirective(f"server-level {d.name}")
        self.root = root or Path(self._server_value("root"))
        self.index = (self._server_value("index") or "index.html").split()[0]
        self.absolute_redirect = self._server_value("absolute_redirect") != "off"
        self.locations = [Location(*(["", d.args[0]] if len(d.args) == 1 else d.args), d.block)
                          for d in self.server if d.name == "location"]
        for loc in self.locations:
            for d in loc.directives:
                if d.name not in KNOWN_LOCATION_DIRECTIVES:
                    raise UnsupportedDirective(f"location {loc.pattern}: {d.name}")

    def _server_value(self, name: str) -> Optional[str]:
        vals = [" ".join(d.args) for d in self.server if d.name == name]
        return vals[0] if vals else None

    # ------------------------------------------------------------------ variables

    def _var(self, name: str, req: Request, uri: str, captures: Dict[str, str]) -> str:
        if name in captures:
            return captures[name]
        if name == "request_uri":
            return req.raw_uri
        if name == "uri":
            return uri
        if name == "args":
            return req.args
        if name == "is_args":
            return "?" if req.args else ""
        if name == "scheme":
            return req.scheme
        if name == "host":
            return (req.headers.get("host") or "_").split(":")[0].lower()
        if name == "remote_addr":
            return req.remote_addr
        if name == "proxy_add_x_forwarded_for":
            prior = req.headers.get("x-forwarded-for")
            return f"{prior}, {req.remote_addr}" if prior else req.remote_addr
        if name.startswith("http_"):
            return req.headers.get(name[5:].replace("_", "-"), "")
        if name in self.maps:
            return self._eval_map(name, req, uri)
        raise UnsupportedDirective(f"variable ${name}")

    def _eval_map(self, name: str, req: Request, uri: str) -> str:
        source, entries = self.maps[name]
        value = self.interpolate(source, req, uri, {})
        default = next((v for k, v in entries if k == "default"), "")
        for key, result in entries:  # exact strings first
            if key != "default" and not key.startswith("~") and key == value:
                return self.interpolate(result, req, uri, {})
        for key, result in entries:
            if key.startswith("~"):
                m = pcre(key).search(value)
                if m:
                    caps = {k: (v or "") for k, v in m.groupdict().items()}
                    caps.update({str(i): (g or "") for i, g in enumerate(m.groups(), start=1)})
                    return self.interpolate(result, req, uri, caps)
        return self.interpolate(default, req, uri, {})

    def interpolate(self, text: str, req: Request, uri: str, captures: Dict[str, str]) -> str:
        if "${" in text:
            raise UnsupportedDirective(f"unrendered placeholder in {text!r}")
        return re.sub(r"\$([A-Za-z_][A-Za-z0-9_]*)", lambda m: self._var(m.group(1), req, uri, captures), text)

    # ------------------------------------------------------------------ locations

    def select(self, uri: str) -> Location:
        for loc in self.locations:
            if loc.modifier == "=" and loc.pattern == uri:
                return loc
        prefixes = [loc for loc in self.locations if loc.modifier in ("", "^~") and uri.startswith(loc.pattern)]
        best = max(prefixes, key=lambda loc: len(loc.pattern)) if prefixes else None
        if best is not None and best.modifier == "^~":
            return best
        for loc in self.locations:
            if loc.modifier in ("~", "~*") and pcre(loc.modifier + loc.pattern).search(uri):
                return loc
        if best is None:
            raise AssertionError(f"no location for {uri}")
        return best

    def handle(self, req: Request) -> Result:
        uri = normalize_uri(req.raw_path)
        if uri is None:
            return Result("return", status=400)
        trace: List[str] = []
        for _ in range(10):  # nginx caps internal redirects at 10
            loc = self.select(uri)
            trace.append(f"{loc.modifier or 'prefix'} {loc.pattern}".strip())
            result = self._run(loc, req, uri)
            if isinstance(result, str):  # internal redirect
                uri = result
                continue
            result.trace = trace
            result.location_block = loc
            result.headers = self._add_headers(loc)
            return result
        raise AssertionError("internal redirect loop")

    def _add_headers(self, loc: Location) -> Dict[str, str]:
        level = loc.directive("add_header") or [d for d in self.server if d.name == "add_header"]
        return {d.args[0]: d.args[1] for d in level}

    def _redirect(self, status: int, target: str, req: Request) -> Result:
        if target.startswith("/") and self.absolute_redirect:
            target = f"{req.scheme}://{(req.headers.get('host') or 'localhost')}{target}"
        return Result("return", status=status, location=target)

    def _run(self, loc: Location, req: Request, uri: str):
        for d in loc.directives:  # rewrite phase: if/return in order
            if d.name == "if":
                cond = " ".join(d.args)
                m = re.fullmatch(r"\(\s*(\$\w+)\s*(=|!=)\s*(\S*)\s*\)", cond)
                if not m:
                    raise UnsupportedDirective(f"if {cond}")
                left = self.interpolate(m.group(1), req, uri, {})
                right = m.group(3).strip('"')
                if (left == right) == (m.group(2) == "="):
                    for inner in d.block:
                        if inner.name != "return":
                            raise UnsupportedDirective(f"if body {inner.name}")
                        return self._return(inner, req, uri)
            elif d.name == "return":
                return self._return(d, req, uri)
        proxy = loc.directive("proxy_pass")
        if proxy:
            return self._proxy(loc, proxy[0].args[0], req, uri)
        try_files = loc.directive("try_files")
        if try_files:
            *candidates, last = try_files[0].args
            for cand in candidates:
                path = self.interpolate(cand, req, uri, {})
                fs = self.root / path.lstrip("/")
                if path.endswith("/") and fs.is_dir():
                    return self._static(path, req)
                if not path.endswith("/") and fs.is_file():
                    return Result("static", file=fs)
            if last.startswith("="):
                return Result("return", status=int(last[1:]))
            return self.interpolate(last, req, uri, {})
        return self._static(uri, req)

    def _return(self, d: Directive, req: Request, uri: str) -> Result:
        status = int(d.args[0])
        arg = self.interpolate(d.args[1], req, uri, {}) if len(d.args) > 1 else ""
        if status in REDIRECT_CODES:
            return self._redirect(status, arg, req)
        return Result("return", status=status, body=arg)

    def _static(self, uri: str, req: Request):
        fs = self.root / uri.lstrip("/")
        if fs.is_dir():
            if not uri.endswith("/"):
                return self._redirect(301, uri + "/" + (f"?{req.args}" if req.args else ""), req)
            if (fs / self.index).is_file():
                return uri + self.index  # index module: internal redirect
            return Result("return", status=403)
        if fs.is_file():
            return Result("static", file=fs)
        return Result("return", status=404)

    def _proxy(self, loc: Location, target: str, req: Request, uri: str) -> Result:
        m = re.fullmatch(r"http://([^/$]+)(.*)", target)
        if not m:
            raise UnsupportedDirective(f"proxy_pass {target}")
        upstream, rest = m.groups()
        if "$" in rest:
            proxy_uri = self.interpolate(rest, req, uri, {})
        elif rest:
            if loc.modifier not in ("", "^~"):
                raise UnsupportedDirective("proxy_pass with URI in a regex location")
            proxy_uri = rest + uri[len(loc.pattern):] + (f"?{req.args}" if req.args else "")
        else:
            proxy_uri = req.raw_uri
        servers = self.upstreams.get(upstream)
        if servers is None:
            raise UnsupportedDirective(f"proxy_pass host {upstream} is not an upstream block")
        headers = {k: v for k, v in req.headers.items()}
        headers["host"] = upstream
        headers["connection"] = "close"
        level = loc.directive("proxy_set_header") or [d for d in self.server if d.name == "proxy_set_header"]
        for d in level:
            value = self.interpolate(d.args[1], req, uri, {})
            if value == "":
                headers.pop(d.args[0].lower(), None)
            else:
                headers[d.args[0].lower()] = value
        return Result("proxy", upstream=servers[0], proxy_uri=proxy_uri, proxy_headers=headers)


# ---------------------------------------------------------------------------- ASGI gateway


def uvicorn_scope(base: dict, raw_uri: str, headers: Dict[str, str], root_path: str) -> dict:
    """The scope uvicorn's h11 protocol builds (``uvicorn/protocols/http/h11_impl.py``)."""
    raw_path, _, query = raw_uri.partition("?")
    path = unquote(raw_path)
    return {
        "type": "http",
        "asgi": base.get("asgi", {"version": "3.0"}),
        "http_version": "1.1",
        "server": ("127.0.0.1", 8090),
        "client": ("127.0.0.1", 40000),  # the in-image gateway connects over loopback
        "scheme": "http",
        "method": base["method"],
        "root_path": root_path,
        "path": root_path + path,
        "raw_path": root_path.encode("ascii") + raw_path.encode("latin-1"),
        "query_string": query.encode("latin-1"),
        "headers": [(k.encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()],
        "state": {},
    }


class GatewayASGI:
    """Serve the modelled gateway as an ASGI app; proxied requests go to ``upstreams[host:port]``.

    Each upstream app is wrapped in uvicorn's real ``ProxyHeadersMiddleware`` with the
    default trust (``127.0.0.1``), and receives the scope uvicorn would build with
    ``root_path`` from ``app.modules.deployment.gateway.uvicorn_options``.
    """

    def __init__(self, model: NginxModel, upstreams: Dict[str, Tuple[Callable, str]],
                 remote_addr: str = "203.0.113.7") -> None:
        from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

        self.model = model
        self.upstreams = {k: (ProxyHeadersMiddleware(app, trusted_hosts="127.0.0.1"), rp)
                          for k, (app, rp) in upstreams.items()}
        self.remote_addr = remote_addr
        self.log: List[Tuple[Request, Result]] = []

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        raw_path = scope.get("raw_path") or scope["path"].encode("latin-1")
        raw_uri = raw_path.decode("latin-1") + (f"?{scope['query_string'].decode('latin-1')}"
                                                 if scope.get("query_string") else "")
        headers: Dict[str, str] = {}
        for k, v in scope["headers"]:
            name = k.decode("latin-1").lower()
            headers[name] = (headers[name] + ", " if name in headers else "") + v.decode("latin-1")
        req = Request(scope["method"], raw_uri, headers, scheme=scope.get("scheme", "http"),
                      remote_addr=self.remote_addr)
        result = self.model.handle(req)
        self.log.append((req, result))
        if result.kind == "proxy":
            app, root_path = self.upstreams[result.upstream]
            inner = uvicorn_scope(scope, result.proxy_uri, result.proxy_headers, root_path)
            await app(inner, receive, send)
            return
        extra = [(k.lower().encode(), v.encode()) for k, v in result.headers.items()]
        if result.kind == "static":
            body = result.file.read_bytes()
            ctype = {".html": "text/html", ".js": "application/javascript", ".svg": "image/svg+xml",
                     ".json": "application/json", ".css": "text/css"}.get(result.file.suffix, "application/octet-stream")
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", ctype.encode())] + extra})
            await send({"type": "http.response.body", "body": body})
            return
        hdrs = [(b"content-type", b"text/plain")] + extra
        if result.location:
            hdrs.append((b"location", result.location.encode("latin-1")))
        await send({"type": "http.response.start", "status": result.status, "headers": hdrs})
        await send({"type": "http.response.body", "body": result.body.encode()})
