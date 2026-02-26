import pytest
import msgspec
from khorsyio.core.http import Request, Response, Router, CorsConfig, HttpApp

@pytest.mark.asyncio
async def test_request_parsing(scope, receive_json):
    req = Request(scope, receive_json)
    assert req.method == "GET"
    assert req.path == "/test"
    assert req.header("host") == "localhost"
    assert req.header("Content-Type") == "application/json"
    assert req.cookie("session") == "abc"
    assert req.cookie("user") == "123"
    assert req.param("a") == "1"
    assert req.param("b") == "2"
    
    body = await req.body()
    assert body == b'{"key": "value"}'
    
    data = await req.json()
    assert data == {"key": "value"}

@pytest.mark.asyncio
async def test_response_json(send):
    await Response.json(send, {"status": "ok"}, status=201, headers={"X-Test": "val"})
    
    assert len(send.messages) == 2
    start = send.messages[0]
    assert start["type"] == "http.response.start"
    assert start["status"] == 201
    headers = {k.decode(): v.decode() for k, v in start["headers"]}
    assert headers["content-type"] == "application/json"
    assert headers["X-Test"] == "val"
    
    body = send.messages[1]
    assert body["type"] == "http.response.body"
    assert body["body"] == b'{"status":"ok"}'

@pytest.mark.asyncio
async def test_router_matching():
    router = Router()
    async def handler(req, send): pass
    
    router.get("/users/{id}", handler)
    router.post("/items", handler)
    
    h, params = router.resolve("GET", "/users/42")
    assert h == handler
    assert params == {"id": "42"}
    
    h, params = router.resolve("POST", "/items")
    assert h == handler
    assert params == {}
    
    h, params = router.resolve("GET", "/none")
    assert h is None

@pytest.mark.asyncio
async def test_router_middleware(scope, send):
    router = Router()
    mw_called = False
    
    async def mw(req):
        nonlocal mw_called
        mw_called = True
        return True
    
    router.use(mw)
    async def handler(req, send):
        await Response.ok(send)
    
    router.get("/test", handler)
    app = HttpApp(router)
    
    await app(scope, None, send)
    assert mw_called is True

@pytest.mark.asyncio
async def test_cors(scope, send):
    cors = CorsConfig(origins=["http://localhost:3000"], credentials=True)
    router = Router()
    async def handler(req, send):
        await Response.ok(send)
    router.get("/test", handler)
    
    app = HttpApp(router, cors=cors)
    
    # Preflight
    preflight_scope = scope.copy()
    preflight_scope["method"] = "OPTIONS"
    await app(preflight_scope, None, send)
    
    start = send.messages[0]
    assert start["status"] == 204
    headers = {k.decode(): v.decode() for k, v in start["headers"]}
    assert headers["access-control-allow-origin"] == "http://localhost:3000"
    assert headers["access-control-allow-credentials"] == "true"
