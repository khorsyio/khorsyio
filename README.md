# khorsyio

Async event-driven python framework. ASGI, socketio, msgspec, SQLAlchemy 2.0 (async with asyncpg).

## Документация

- Общее оглавление docs: [docs/README.md](docs/README.md)
- Быстрый старт: [docs/getting_started.md](docs/getting_started.md)
- Архитектура: [docs/architecture.md](docs/architecture.md)
- Приложение App и жизненный цикл: [docs/app.md](docs/app.md)
- Настройки окружения: [docs/settings.md](docs/settings.md)
- HTTP API: [docs/http.md](docs/http.md)
- Шина событий Bus, метрики, журнал: [docs/bus.md](docs/bus.md)
- События и структуры: [docs/events.md](docs/events.md)
- Обработчики и DI: [docs/handlers.md](docs/handlers.md)
- Домены и namespace: [docs/domain.md](docs/domain.md)
- WebSocket транспорт: [docs/transport.md](docs/transport.md)
- HTTP клиент: [docs/client.md](docs/client.md)
- База данных: [docs/db.md](docs/db.md)
- Хелперы SQLAlchemy: [docs/query.md](docs/query.md)
- Многоядерная архитектура (Multiprocessing): [docs/multi_core.md](docs/multi_core.md)

### Паттерны и методология
- Методология декомпозиции: [docs/decomposition.md](docs/decomposition.md)
- Шаблоны кода и рецепты: [docs/templates.md](docs/templates.md)
- Анализ применимости и архитектурные паттерны: [docs/architecture_patterns.md](docs/architecture_patterns.md)
- Руководство для LLM агентов: [docs/llm_guidelines.md](docs/llm_guidelines.md)

## Установка

```
pip install .
pip install ".[granian]"
pip install ".[uvicorn]"
```

## Быстрый старт

```python
from khorsyio import App, Response, CorsConfig

app = App(cors=CorsConfig())
app.router.get("/health", lambda req, send: Response.ok(send, status="ok"))

if __name__ == "__main__":
    app.run()
```

## Структуры

```python
import msgspec

class UserIn(msgspec.Struct):
    name: str
    age: int = 0
```

## Context

Каждое событие несет Context, автоматически прокидывается через цепочку handlers.

```python
# ctx.trace_id  - сквозной id запроса
# ctx.user_id   - id пользователя
# ctx.extra     - произвольные данные (роль, сессия)
# ctx.source    - кто создал событие
# ctx.timestamp - время создания
```

## Error

```python
from khorsyio import Error

# error.code     - строковый код ("timeout", "validation")
# error.message  - описание
# error.source   - источник
# error.details  - dict с дополнительными данными

result = await bus.request("event", data, response_type="event.done")
if result.is_error:
    print(result.error.code, result.error.message)
```

## Handlers

Реализуй process - получи данные и context.

```python
from khorsyio import Handler, Context

class CreateUser(Handler):
    subscribes_to = "user.create"
    publishes = "user.created"
    input_type = UserIn
    output_type = UserOut

    async def process(self, data: UserIn, ctx: Context) -> UserOut:
        return UserOut(id=1, name=data.name)
```

## Handler DI

Имя параметра в __init__ определяет что инжектится.

```python
class MyHandler(Handler):
    subscribes_to = "x"
    publishes = ""

    def __init__(self, db, client):
        self._db = db           # -> app.db
        self._client = client   # -> app.client

    async def process(self, data, ctx): ...
```

Маппинг имен: db -> app.db, client -> app.client, bus -> app.bus, transport -> app.transport, app -> app. Неизвестные имена получают app.

## Domains

Группировка handlers и routes с namespace.

```python
from khorsyio import Domain, Route

users = Domain()
users.namespace = "user"        # handler events получат префикс "user."
users.handlers = [CreateUser]   # классы, не экземпляры
users.routes = [Route("GET", "/users", get_users)]
```

```python
app.mount(users)
# CreateUser.subscribes_to "create" -> "user.create"
# CreateUser.publishes "created" -> "user.created"
```

## Event bus

Публикация

```python
await bus.publish("user.create", UserIn(name="test"), source="http", user_id="u1")
```

Синхронный запрос (publish + wait)

```python
result = await bus.request(
    "user.create", UserIn(name="test"),
    response_type="user.created",
    source="http", user_id="u1", extra={"role": "admin"},
    timeout=5.0)
```

## Scheduled tasks

```python
app.bus.schedule("system.healthcheck", HealthCheck(), interval=60.0)
```

Публикует событие с заданным интервалом. Handler обрабатывает как обычное событие.

## Http

Request

```python
async def handler(req, send):
    body = await req.json(MyStruct)
    name = req.param("name", "default")
    id = req.path_params["id"]
    token = req.header("authorization")
    session = req.cookie("session")
    bus = req.state["bus"]  # от middleware
```

Response

```python
await Response.ok(send, message="done")
await Response.ok(send, cookies={"session": {"value": "abc", "path": "/", "httponly": True, "max_age": 3600}})
await Response.json(send, data, headers={"x-request-id": "123"})
await Response.error(send, "not found", 404, code="not_found")
```

## CORS

```python
app = App(cors=CorsConfig(
    origins=["http://localhost:3000"],
    credentials=True,
    headers=["content-type", "authorization"],
    max_age=86400))
```

## Middleware

```python
async def inject_bus(req):
    req.state["bus"] = app.bus

async def auth(req):
    if not req.header("authorization"):
        return False  # -> 403

app.router.use(inject_bus)
app.router.use(auth)
```

## Metrics

```python
app.bus.metrics.snapshot()
# {"HandlerName": {"processed": 10, "errors": 1, "avg_ms": 5.2, "last_error": "..."}}

app.router.get("/metrics", lambda req, send: Response.json(send, app.bus.metrics.snapshot()))
```

## Event log

Последние N событий в памяти для отладки.

```python
app.bus.event_log.recent(50)                          # последние 50
app.bus.event_log.recent(20, event_type="user.create") # по типу
app.bus.event_log.recent(20, trace_id="abc123")        # по trace

app.router.get("/events", lambda req, send: Response.json(send, app.bus.event_log.recent(
    n=int(req.param("n", "50")),
    event_type=req.param("type"),
    trace_id=req.param("trace"))))
```

Размер буфера настраивается через event_log_size в Bus.

## Websocket

Протокол

```json
{"event_type": "chat.msg", "payload": {"text": "hello"}, "trace_id": "optional", "user_id": "optional"}
```

ws sid сохраняется в ctx.extra["_ws_sid"]. Handler может отправить ответ отправителю через transport.reply_to_sender(envelope).

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

Клиент

```python
from khorsyio import SocketClient
client = SocketClient("http://localhost:8000")
client.on("chat.reply", lambda data: print(data["payload"]))
await client.connect()
await client.send("chat.msg", {"text": "hello"})
```

## Database

Интегрирована SQLAlchemy 2.0 (async) с драйвером asyncpg. Доступны два способа работы:

1) Простой доступ к БД через совместимые методы (обратная совместимость с asyncpg-стилем):

```python
rows = await app.db.fetch("select * from users where id = $1", user_id)
row = await app.db.fetchrow("select * from users where id = $1", user_id)
val = await app.db.fetchval("select count(*) from users where active = $1", True)
res = await app.db.execute("insert into users (name) values ($1)", name)  # -> "OK 1"
```

2) Полноценный SQLAlchemy Core/ORM через сессию:

```python
from sqlalchemy import select
from khorsyio.db import Base  # для моделей ORM при необходимости

async with app.db.session() as s:
    result = await s.execute(select(User).where(User.active == True))
    users = result.scalars().all()
```

Фильтры, сортировка и пагинация:

```python
from sqlalchemy import select
from khorsyio.db import apply_filters, apply_order, paginate

stmt = select(User)
stmt = apply_filters(stmt, User, {"age__gte": 18, "name__icontains": "alex"})
stmt = apply_order(stmt, User, ["-created_at", "id"])  # DESC по created_at, ASC по id

async with app.db.session() as s:
    page = await paginate(s, stmt, page=1, per_page=20)
    # page = {"items": [...], "total": int, "page": int, "per_page": int, "pages": int}
```

Курсорная пагинация (простая):

```python
from khorsyio.db import apply_cursor
stmt = apply_cursor(stmt, User, cursor_value=last_id, cursor_field="id", order="asc")
```


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

## Graceful shutdown

Шина дожидает обработки оставшихся событий (drain_timeout=5s). Pending bus.request получают error с code="shutdown". Scheduled tasks отменяются.
