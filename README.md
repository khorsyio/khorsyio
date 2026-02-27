# khorsyio

Async Python framework for event-driven applications. ASGI, socket.io, msgspec, SQLAlchemy 2.0 async.

Business logic is built from isolated handlers connected through an internal event bus. Each block is a separate class with an explicit input, output, and dependencies. Blocks have no knowledge of each other.

The framework is designed so that building from scratch — including with LLM-assisted development — is as predictable and structured as possible.

---

## Installation

```bash
pip install khorsyio
pip install "khorsyio[granian]"
pip install "khorsyio[uvicorn]"
```

---

## Quick start

```python
from khorsyio import App, Response, CorsConfig

app = App(cors=CorsConfig())
app.router.get("/health", lambda req, send: Response.ok(send, status="ok"))

if __name__ == "__main__":
    app.run()
```

---

## Why it works well with LLM

**Isolated blocks.** Each Handler is self-contained. An LLM generates one block at a time without needing to hold the entire system in context.

**Structs as contracts.** `msgspec.Struct` is simultaneously documentation and validation. The contract is defined before the first line of logic is written. The LLM implements logic against a ready-made contract.

**Event graph as architecture.** Data flow is described with strings `subscribes_to` and `publishes`. Explainable to an LLM in one paragraph — it reproduces the correct chain without routing errors.

**Minimal boilerplate.** Serialization, context, tracing, DI — all automatic. The LLM only writes `process`.

---

## Handlers

```python
import msgspec
from khorsyio import Handler, Context

class UserIn(msgspec.Struct):
    name: str = ""

class UserOut(msgspec.Struct):
    id: int = 0
    name: str = ""

class CreateUser(Handler):
    subscribes_to = "user.create"
    publishes = "user.created"
    input_type = UserIn
    output_type = UserOut

    def __init__(self, db):
        self._db = db  # db -> app.db

    async def process(self, data: UserIn, ctx: Context) -> UserOut:
        row = await self._db.fetchrow(
            "insert into users (name) values ($1) returning id, name", data.name)
        return UserOut(**row)
```

DI by parameter name: `db -> app.db`, `client -> app.client`, `bus -> app.bus`, `transport -> app.transport`, `app -> app`.

---

## Domains

Grouping handlers and routes with a namespace.

```python
from khorsyio import Domain, Route

users = Domain()
users.namespace = "user"
users.handlers = [CreateUser]
users.routes = [Route("GET", "/users", get_users)]

app.mount(users)
# subscribes_to "create" -> "user.create"
# publishes "created"    -> "user.created"
```

---

## Context

Automatically propagated through the entire handler chain.

```python
ctx.trace_id   # request-wide trace id
ctx.user_id    # user id
ctx.extra      # arbitrary data (role, session)
ctx.source     # who created the event
ctx.timestamp  # creation time
```

---

## Event bus

```python
# publish
await bus.publish("user.create", UserIn(name="test"), source="http", user_id="u1")

# request with response wait
result = await bus.request(
    "user.create", UserIn(name="test"),
    response_type="user.created",
    source="http", user_id="u1", timeout=5.0)

if result.is_error:
    print(result.error.code, result.error.message)
```

Scheduled tasks:

```python
app.bus.schedule("system.healthcheck", HealthCheck(), interval=60.0)
```

---

## HTTP

```python
async def handler(req, send):
    body  = await req.json(MyStruct)
    name  = req.param("name", "default")
    id    = req.path_params["id"]
    token = req.header("authorization")
    bus   = req.state["bus"]

await Response.ok(send, message="done")
await Response.json(send, data, headers={"x-request-id": "123"})
await Response.error(send, "not found", 404, code="not_found")
await Response.ok(send, cookies={"session": {"value": "abc", "path": "/", "httponly": True, "max_age": 3600}})
```

CORS:

```python
app = App(cors=CorsConfig(
    origins=["http://localhost:3000"],
    credentials=True,
    headers=["content-type", "authorization"],
    max_age=86400))
```

Middleware:

```python
async def inject_bus(req):
    req.state["bus"] = app.bus

async def auth(req):
    if not req.header("authorization"):
        return False  # -> 403

app.router.use(inject_bus)
app.router.use(auth)
```

---

## WebSocket

Incoming message protocol:

```json
{"event_type": "chat.msg", "payload": {"text": "hello"}, "trace_id": "optional", "user_id": "optional"}
```

Client `sid` is stored in `ctx.extra["_ws_sid"]`. Replying to sender:

```python
class ReplyHandler(Handler):
    subscribes_to = "chat.reply"
    publishes = ""

    def __init__(self, transport):
        self._transport = transport

    async def process(self, data, ctx):
        if isinstance(data, Envelope):
            await self._transport.reply_to_sender(data)
```

Client:

```python
from khorsyio import SocketClient

client = SocketClient("http://localhost:8000")
client.on("chat.reply", lambda data: print(data["payload"]))
await client.connect()
await client.send("chat.msg", {"text": "hello"})
```

---

## Database

Simple methods (asyncpg-style):

```python
rows = await self._db.fetch("select * from users where active = $1", True)
row  = await self._db.fetchrow("select * from users where id = $1", user_id)
val  = await self._db.fetchval("select count(*) from users")
res  = await self._db.execute("insert into users (name) values ($1)", name)
```

SQLAlchemy ORM via session:

```python
from sqlalchemy import select

async with app.db.session() as s:
    result = await s.execute(select(User).where(User.active == True))
    users = result.scalars().all()
```

Filters, sorting, pagination:

```python
from khorsyio.db import apply_filters, apply_order, paginate, apply_cursor

stmt = select(User)
stmt = apply_filters(stmt, User, {"age__gte": 18, "name__icontains": "alex"})
stmt = apply_order(stmt, User, ["-created_at", "id"])

async with app.db.session() as s:
    page = await paginate(s, stmt, page=1, per_page=20)
    # {"items": [...], "total": int, "page": int, "per_page": int, "pages": int}

# cursor pagination
stmt = apply_cursor(stmt, User, cursor_value=last_id, cursor_field="id", order="asc")
```

---

## Metrics and Event log

```python
app.bus.metrics.snapshot()
# {"HandlerName": {"processed": 10, "errors": 1, "avg_ms": 5.2}}

app.bus.event_log.recent(50)
app.bus.event_log.recent(20, event_type="user.create")
app.bus.event_log.recent(20, trace_id="abc123")

app.router.get("/metrics", lambda req, send: Response.json(send, app.bus.metrics.snapshot()))
app.router.get("/events",  lambda req, send: Response.json(send, app.bus.event_log.recent(50)))
```

---

## Multiprocessing

CPU-bound handlers run in separate processes without changing any other code:

```python
class HeavyHandler(Handler):
    subscribes_to = "heavy.task"
    publishes = "heavy.result"
    execution_mode = "process"

    async def process(self, data, ctx):
        return TaskResult(value=some_heavy_math(data.n))
```

---

## Settings

```
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
SERVER_DEBUG=false
SERVER_WORKERS=1
DB_DSN=postgresql+asyncpg://localhost:5432/khorsyio
DB_POOL_MIN=2
DB_POOL_MAX=10
BUS_HANDLER_TIMEOUT=30.0
```

---

## Graceful shutdown

The bus waits for the queue to drain (`drain_timeout=5s`). Pending `bus.request` calls receive an error with `code="shutdown"`. Scheduled tasks are cancelled.

---

## Comparison with alternatives

There is no direct equivalent in Python. The closest tools each solve only part of the problem.

`python-cqrs`, `pymediator`, `python-mediator` implement the mediator pattern with typed handlers — conceptually close. But these are libraries without HTTP, WebSocket, DB, or a scheduler. You still need to assemble a stack on top using FastAPI or Litestar, SQLAlchemy, APScheduler, and python-socketio.

`FastAPI` + `Litestar` are mature HTTP frameworks with DI and type safety. Litestar uses the same `msgspec`. But they solve the HTTP API problem, not the event chain problem. The internal bus with blocks still needs to be built on top of them manually.

`bubus`, `messagebus` are production async event bus libraries. More mature on the bus side, but without an HTTP layer or DB.

The real distinction of khorsyio is full-stack cohesion: in-process event bus, HTTP ASGI, WebSocket, DB, HTTP client, scheduler, DI, and multiprocessing in a single package with a single pattern. The alternative is a stack of 5-6 separate libraries, each with its own integration patterns.

---

## TODO

**OpenAPI / Swagger.** Auto-generation of documentation from types. FastAPI and Litestar do this — khorsyio does not. A significant gap for API-first development.

**Testing utilities.** No test client for HTTP, no mock bus for isolated handler testing.

**Type-safe DI.** DI by string name is not verified at startup. A typo silently injects `app` instead of the intended dependency.

**Retry and dead letter queue.** No built-in retry policy on handler failure and no mechanism for storing unprocessed events.

**Observability.** Metrics and event log exist, but there is no integration with OpenTelemetry, Prometheus, or structured logging.

---

## Documentation

- [Documentation](https://github.com/khorsyio/khorsyio-docs)

---

## License

MIT