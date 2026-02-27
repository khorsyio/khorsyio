# khorsyio

Async Python фреймворк для событийно-ориентированных приложений. ASGI, socket.io, msgspec, SQLAlchemy 2.0 async.

Бизнес-логика строится из изолированных обработчиков (Handler), связанных через внутреннюю шину событий. Каждый блок — отдельный класс с явным входом, выходом и зависимостями. Блоки не знают друг о друге.

Фреймворк спроектирован так, чтобы разработка с нуля — в том числе с помощью LLM-инструментов — была максимально предсказуемой и структурированной.

---

## Установка

```bash
pip install khorsyio
pip install "khorsyio[granian]"
pip install "khorsyio[uvicorn]"
```

---

## Быстрый старт

```python
from khorsyio import App, Response, CorsConfig

app = App(cors=CorsConfig())
app.router.get("/health", lambda req, send: Response.ok(send, status="ok"))

if __name__ == "__main__":
    app.run()
```

---

## Почему это удобно для разработки с LLM

**Изолированные блоки.** Каждый Handler самодостаточен. LLM генерирует один блок за раз без необходимости удерживать в контексте всю систему.

**Структуры как контракт.** `msgspec.Struct` — это одновременно документация и валидация. Контракт задаётся до того, как написана первая строка логики. LLM реализует логику под готовый контракт.

**Граф событий как архитектура.** Поток данных описывается строками `subscribes_to` и `publishes`. Объясняется LLM в одном абзаце — она воспроизводит правильную цепочку без ошибок в маршрутизации.

**Минимальный boilerplate.** Сериализация, контекст, трассировка, DI — всё автоматически. LLM пишет только `process`.

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

DI по имени параметра: `db -> app.db`, `client -> app.client`, `bus -> app.bus`, `transport -> app.transport`, `app -> app`.

---

## Domains

Группировка handlers и routes с namespace.

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

Автоматически прокидывается через всю цепочку handlers.

```python
ctx.trace_id   # сквозной id запроса
ctx.user_id    # id пользователя
ctx.extra      # произвольные данные (роль, сессия)
ctx.source     # кто создал событие
ctx.timestamp  # время создания
```

---

## Event bus

```python
# публикация
await bus.publish("user.create", UserIn(name="test"), source="http", user_id="u1")

# запрос с ожиданием ответа
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

Протокол входящего сообщения:

```json
{"event_type": "chat.msg", "payload": {"text": "hello"}, "trace_id": "optional", "user_id": "optional"}
```

`sid` клиента сохраняется в `ctx.extra["_ws_sid"]`. Ответ отправителю:

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

Клиент:

```python
from khorsyio import SocketClient

client = SocketClient("http://localhost:8000")
client.on("chat.reply", lambda data: print(data["payload"]))
await client.connect()
await client.send("chat.msg", {"text": "hello"})
```

---

## Database

Простые методы (asyncpg-стиль):

```python
rows = await self._db.fetch("select * from users where active = $1", True)
row  = await self._db.fetchrow("select * from users where id = $1", user_id)
val  = await self._db.fetchval("select count(*) from users")
res  = await self._db.execute("insert into users (name) values ($1)", name)
```

SQLAlchemy ORM через сессию:

```python
from sqlalchemy import select

async with app.db.session() as s:
    result = await s.execute(select(User).where(User.active == True))
    users = result.scalars().all()
```

Фильтры, сортировка, пагинация:

```python
from khorsyio.db import apply_filters, apply_order, paginate, apply_cursor

stmt = select(User)
stmt = apply_filters(stmt, User, {"age__gte": 18, "name__icontains": "alex"})
stmt = apply_order(stmt, User, ["-created_at", "id"])

async with app.db.session() as s:
    page = await paginate(s, stmt, page=1, per_page=20)
    # {"items": [...], "total": int, "page": int, "per_page": int, "pages": int}

# курсорная пагинация
stmt = apply_cursor(stmt, User, cursor_value=last_id, cursor_field="id", order="asc")
```

---

## Metrics и Event log

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

CPU-bound обработчики запускаются в отдельных процессах без изменения остального кода:

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

Шина дожидает обработки очереди (`drain_timeout=5s`). Pending `bus.request` получают error с `code="shutdown"`. Scheduled tasks отменяются.

---

## Сравнение с аналогами

В Python нет прямого аналога. Ближайшие инструменты решают только часть задачи.

`python-cqrs`, `pymediator`, `python-mediator` — реализуют паттерн медиатора с типизированными хендлерами, концептуально близко. Но это библиотеки без HTTP, WebSocket, DB и scheduler. Поверх них нужно собирать стек из FastAPI или Litestar, SQLAlchemy, APScheduler и python-socketio.

`FastAPI` + `Litestar` — зрелые HTTP-фреймворки с DI и типизацией. Litestar использует тот же `msgspec`. Но они решают задачу HTTP API, а не событийной цепочки. Внутреннюю шину с блоками нужно строить поверх них самостоятельно.

`bubus`, `messagebus` — production event bus с async. Технически зрелее по части шины, но без HTTP-слоя и DB.

Реальное отличие khorsyio — full-stack монолитность: in-process event bus, HTTP ASGI, WebSocket, DB, HTTP client, scheduler, DI и multiprocessing в одном пакете с единым паттерном. Альтернатива — стек из 5-6 отдельных библиотек со своими паттернами интеграции.

---

## TODO

**OpenAPI / Swagger.** Автогенерация документации из типов. FastAPI и Litestar это делают — khorsyio нет. Существенный пробел для API-first разработки.

**Тестовые утилиты.** Нет test client для HTTP, нет mock bus для изолированного тестирования хендлеров.

**Type-safe DI.** DI по имени строки не верифицируется при старте. Опечатка тихо подставляет `app` вместо нужной зависимости.

**Retry и dead letter queue.** Нет встроенной политики повторных попыток при ошибке хендлера и механизма складирования необработанных событий.

**Observability.** Метрики и event log есть, но нет интеграции с OpenTelemetry, Prometheus или structured logging.

---

## Документация

- [Документация](https://github.com/khorsyio/khorsyio-docs)


---

## Лицензия

MIT