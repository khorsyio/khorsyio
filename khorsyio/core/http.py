from __future__ import annotations
import msgspec
import logging
import time
from http.cookies import SimpleCookie
from urllib.parse import parse_qs
from typing import Callable

log = logging.getLogger("khorsyio.http")


class Request:
    def __init__(self, scope, receive):
        self.scope = scope
        self.method = scope.get("method", "GET")
        self.path = scope.get("path", "/")
        self.headers = {k.decode(): v.decode() for k, v in scope.get("headers", [])}
        self.query = parse_qs(scope.get("query_string", b"").decode())
        self.path_params: dict = {}
        self.state: dict = {}
        self._receive = receive
        self._body: bytes | None = None
        self._cookies: dict | None = None

    @property
    def cookies(self) -> dict[str, str]:
        if self._cookies is None:
            self._cookies = {}
            raw = self.headers.get("cookie", "")
            if raw:
                sc = SimpleCookie(raw)
                self._cookies = {k: v.value for k, v in sc.items()}
        return self._cookies

    def cookie(self, key: str, default: str | None = None) -> str | None:
        return self.cookies.get(key, default)

    def header(self, key: str, default: str | None = None) -> str | None:
        return self.headers.get(key.lower(), default)

    async def body(self) -> bytes:
        if self._body is None:
            chunks = []
            while True:
                msg = await self._receive()
                chunks.append(msg.get("body", b""))
                if not msg.get("more_body", False):
                    break
            self._body = b"".join(chunks)
        return self._body

    async def json(self, typ: type | None = None):
        raw = await self.body()
        if typ:
            return msgspec.json.decode(raw, type=typ)
        return msgspec.json.decode(raw)

    def param(self, key: str, default: str | None = None) -> str | None:
        vals = self.query.get(key)
        return vals[0] if vals else default


def _build_headers(content_type: str, headers: dict | None = None, cookies: dict | None = None) -> list:
    h = [[b"content-type", content_type.encode()]]
    if headers:
        for k, v in headers.items():
            h.append([k.encode() if isinstance(k, str) else k, v.encode() if isinstance(v, str) else v])
    if cookies:
        for name, opts in cookies.items():
            if isinstance(opts, str):
                opts = {"value": opts}
            parts = [f"{name}={opts['value']}"]
            for attr, label in [("path", "Path"), ("domain", "Domain"), ("max_age", "Max-Age"), ("expires", "Expires")]:
                if attr in opts:
                    parts.append(f"{label}={opts[attr]}")
            if opts.get("httponly"):
                parts.append("HttpOnly")
            if opts.get("secure"):
                parts.append("Secure")
            parts.append(f"SameSite={opts.get('samesite', 'Lax')}")
            h.append([b"set-cookie", "; ".join(parts).encode()])
    return h


class Response:
    @staticmethod
    async def json(send, data, status=200, headers: dict | None = None, cookies: dict | None = None):
        body = msgspec.json.encode(data) if not isinstance(data, bytes) else data
        h = _build_headers("application/json", headers, cookies)
        await send({"type": "http.response.start", "status": status, "headers": h})
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def ok(send, headers: dict | None = None, cookies: dict | None = None, **kwargs):
        await Response.json(send, kwargs, headers=headers, cookies=cookies)

    @staticmethod
    async def text(send, text: str, status=200, headers: dict | None = None, cookies: dict | None = None):
        h = _build_headers("text/plain", headers, cookies)
        await send({"type": "http.response.start", "status": status, "headers": h})
        await send({"type": "http.response.body", "body": text.encode()})

    @staticmethod
    async def error(send, message: str, status=400, code: str = "error"):
        await Response.json(send, {"error": message, "code": code}, status)


class Route:
    def __init__(self, method: str, path: str, handler: Callable):
        self.method = method.upper()
        self.path = path
        self.handler = handler


class CorsConfig:
    def __init__(self, origins: list[str] | str = "*", methods: list[str] | None = None,
                 headers: list[str] | None = None, credentials: bool = False, max_age: int = 86400):
        self.origins = origins if isinstance(origins, list) else [origins]
        self.methods = methods or ["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"]
        self.headers = headers or ["content-type", "authorization", "x-request-id"]
        self.credentials = credentials
        self.max_age = max_age

    def allowed_origin(self, origin: str) -> str | None:
        if "*" in self.origins:
            return "*"
        if origin in self.origins:
            return origin
        return None


class Router:
    def __init__(self):
        self._routes: dict[str, dict[str, Callable]] = {}
        self._middleware: list[Callable] = []
        self._after: list[Callable] = []

    def use(self, middleware: Callable):
        self._middleware.append(middleware)

    def after(self, hook: Callable):
        self._after.append(hook)

    def add(self, method: str, path: str, handler: Callable):
        if path not in self._routes:
            self._routes[path] = {}
        self._routes[path][method.upper()] = handler

    def mount(self, routes: list[Route]):
        for r in routes:
            self.add(r.method, r.path, r.handler)

    def get(self, path: str, handler: Callable):
        self.add("GET", path, handler)

    def post(self, path: str, handler: Callable):
        self.add("POST", path, handler)

    def put(self, path: str, handler: Callable):
        self.add("PUT", path, handler)

    def delete(self, path: str, handler: Callable):
        self.add("DELETE", path, handler)

    def resolve(self, method: str, path: str) -> tuple[Callable | None, dict]:
        if path in self._routes:
            handler = self._routes[path].get(method.upper())
            if handler:
                return handler, {}
        for pattern, methods in self._routes.items():
            params = self._match(pattern, path)
            if params is not None:
                handler = methods.get(method.upper())
                if handler:
                    return handler, params
        return None, {}

    def _match(self, pattern: str, path: str) -> dict | None:
        p_parts = pattern.strip("/").split("/")
        r_parts = path.strip("/").split("/")
        if len(p_parts) != len(r_parts):
            return None
        params = {}
        for pp, rp in zip(p_parts, r_parts):
            if pp.startswith("{") and pp.endswith("}"):
                params[pp[1:-1]] = rp
            elif pp != rp:
                return None
        return params


class HttpApp:
    def __init__(self, router: Router, cors: CorsConfig | None = None):
        self.router = router
        self.cors = cors

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        req = Request(scope, receive)

        if self.cors:
            origin = req.header("origin", "")
            allowed = self.cors.allowed_origin(origin) if origin else None
            if req.method == "OPTIONS" and origin:
                cors_headers = self._cors_headers(allowed)
                await send({"type": "http.response.start", "status": 204, "headers": cors_headers})
                await send({"type": "http.response.body", "body": b""})
                return

        for mw in self.router._middleware:
            result = await mw(req)
            if result is False:
                await Response.error(send, "forbidden", 403, code="forbidden")
                return

        handler, params = self.router.resolve(req.method, req.path)
        if handler is None:
            await Response.error(send, "not found", 404, code="not_found")
            return
        req.path_params = params
        req.scope["path_params"] = params

        t0 = time.time()
        req.state["start_time"] = t0

        original_send = send
        if self.cors:
            origin = req.header("origin", "")
            allowed = self.cors.allowed_origin(origin) if origin else None
            async def cors_send(message):
                if message["type"] == "http.response.start" and allowed:
                    extra = self._cors_headers(allowed)
                    message["headers"] = message.get("headers", []) + extra
                await original_send(message)
            send = cors_send

        try:
            status_tracker = {"status": 200}
            original_for_track = send
            async def tracking_send(message):
                if message["type"] == "http.response.start":
                    status_tracker["status"] = message.get("status", 200)
                await original_for_track(message)
            if self.cors:
                _prev_send = send
                async def combined_send(message):
                    if message["type"] == "http.response.start":
                        status_tracker["status"] = message.get("status", 200)
                    await _prev_send(message)
                send = combined_send
            else:
                send = tracking_send

            await handler(req, send)
            dt = round((time.time() - t0) * 1000, 1)
            req.state["duration_ms"] = dt
            req.state["status"] = status_tracker["status"]
            log.debug(f"{req.method} {req.path} {status_tracker['status']} {dt}ms")
            for hook in self.router._after:
                try:
                    result = hook(req)
                    if hasattr(result, "__await__"):
                        await result
                except Exception as e:
                    log.error(f"after hook error err={e}")
        except msgspec.ValidationError as e:
            log.error(f"validation error path={req.path} err={e}")
            await Response.error(send, str(e), 422, code="validation")
        except Exception as e:
            log.error(f"handler error path={req.path} err={e}")
            await Response.error(send, "internal error", 500, code="internal")

    def _cors_headers(self, allowed_origin: str | None) -> list:
        if not allowed_origin or not self.cors:
            return []
        h = [
            [b"access-control-allow-origin", allowed_origin.encode()],
            [b"access-control-allow-methods", ", ".join(self.cors.methods).encode()],
            [b"access-control-allow-headers", ", ".join(self.cors.headers).encode()],
            [b"access-control-max-age", str(self.cors.max_age).encode()],
        ]
        if self.cors.credentials:
            h.append([b"access-control-allow-credentials", b"true"])
        return h
