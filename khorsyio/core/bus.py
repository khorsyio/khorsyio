import asyncio
import logging
import multiprocessing
import os
import signal
import sys
import time
from collections import defaultdict, deque
from khorsyio.core.structs import Envelope, Error, Context
from khorsyio.core.handler import Handler
from khorsyio.core.worker import Worker
import msgspec

log = logging.getLogger("khorsyio.bus")


def _executor_run(payload: bytes) -> bytes:
    import importlib
    import asyncio
    import msgspec
    from khorsyio.core.structs import Envelope
    
    task_data = msgspec.msgpack.decode(payload)
    module_name = task_data["module"]
    class_name = task_data["class"]
    
    # decode envelope with its strict type
    env_data = msgspec.msgpack.encode(task_data["envelope"])
    envelope = msgspec.msgpack.decode(env_data, type=Envelope)

    mod = importlib.import_module(module_name)
    handler_cls = getattr(mod, class_name)
    handler = handler_cls()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(handler.handle(envelope))
        return msgspec.msgpack.encode(result)
    finally:
        loop.close()


class ProcessPool:
    def __init__(self, size: int):
        self.size = size
        self.executor = None

    def start(self):
        import concurrent.futures
        ctx = multiprocessing.get_context("spawn")
        self.executor = concurrent.futures.ProcessPoolExecutor(
            max_workers=self.size, 
            mp_context=ctx
        )
        log.info(f"ProcessPool started with {self.size} workers (ProcessPoolExecutor)")

    def stop(self):
        if self.executor:
            import sys
            if sys.version_info >= (3, 9):
                self.executor.shutdown(wait=False, cancel_futures=True)
            else:
                self.executor.shutdown(wait=False)
            self.executor = None

    async def run_task(self, handler_module: str, handler_class: str, envelope: Envelope) -> Envelope:
        loop = asyncio.get_running_loop()
        task_data = {
            "module": handler_module,
            "class": handler_class,
            "envelope": envelope
        }
        payload = msgspec.msgpack.encode(task_data)
        
        # Отправляем задачу в экзекьютор
        res_bytes = await loop.run_in_executor(self.executor, _executor_run, payload)
        
        return msgspec.msgpack.decode(res_bytes, type=Envelope)


class Metrics:
    def __init__(self):
        self.processed: dict[str, int] = defaultdict(int)
        self.errors: dict[str, int] = defaultdict(int)
        self.total_ms: dict[str, float] = defaultdict(float)
        self.last_error: dict[str, str] = {}

    def record(self, handler_name: str, duration_ms: float, ok: bool, error: str = ""):
        self.processed[handler_name] += 1
        self.total_ms[handler_name] += duration_ms
        if not ok:
            self.errors[handler_name] += 1
            self.last_error[handler_name] = error

    def avg_ms(self, handler_name: str) -> float:
        count = self.processed.get(handler_name, 0)
        return round(self.total_ms[handler_name] / count, 2) if count else 0.0

    def snapshot(self) -> dict:
        result = {}
        for name in self.processed:
            result[name] = {
                "processed": self.processed[name],
                "errors": self.errors.get(name, 0),
                "avg_ms": self.avg_ms(name),
            }
            if name in self.last_error:
                result[name]["last_error"] = self.last_error[name]
        return result


class EventLog:
    def __init__(self, max_size: int = 200):
        self._buffer: deque = deque(maxlen=max_size)

    def record(self, envelope: Envelope, handler_name: str = "", duration_ms: float = 0, ok: bool = True):
        self._buffer.append({
            "ts": time.time(),
            "event_type": envelope.event_type,
            "trace_id": envelope.trace_id,
            "source": envelope.ctx.source,
            "handler": handler_name,
            "duration_ms": duration_ms,
            "ok": ok,
            "error": envelope.error.message if envelope.error else "",
        })

    def recent(self, n: int = 50, event_type: str | None = None, trace_id: str | None = None) -> list[dict]:
        items = list(self._buffer)
        if event_type:
            items = [e for e in items if e["event_type"] == event_type]
        if trace_id:
            items = [e for e in items if e["trace_id"] == trace_id]
        return items[-n:]

    def snapshot(self) -> list[dict]:
        return list(self._buffer)


class ScheduledTask:
    def __init__(self, event_type: str, data: msgspec.Struct, interval: float, source: str = "scheduler"):
        self.event_type = event_type
        self.data = data
        self.interval = interval
        self.source = source
        self._task: asyncio.Task | None = None


class Bus:
    def __init__(self, app=None, handler_timeout: float = 30.0, event_log_size: int = 200, pool_size: int | None = None):
        self.app = app
        self._pool = ProcessPool(pool_size or os.cpu_count() or 4)
        self._subs: dict[str, list[Handler]] = defaultdict(list)
        self._queue: asyncio.Queue[Envelope] = None  # type: ignore
        self._running = False
        self._draining = False
        self._error_callbacks: list = []
        self._default_timeout = handler_timeout
        self._waiters: dict[str, asyncio.Future] = {}
        self._scheduled: list[ScheduledTask] = []
        self.metrics = Metrics()
        self.event_log = EventLog(max_size=event_log_size)

    def register(self, handler: Handler):
        if handler.timeout == 30.0 and self._default_timeout != 30.0:
            handler.timeout = self._default_timeout
        self._subs[handler.subscribes_to].append(handler)
        log.info(f"registered {handler.__class__.__name__} on '{handler.subscribes_to}' -> '{handler.publishes}'")

    def schedule(self, event_type: str, data: msgspec.Struct, interval: float, source: str = "scheduler"):
        self._scheduled.append(ScheduledTask(event_type, data, interval, source))
        log.info(f"scheduled '{event_type}' every {interval}s")

    def on_error(self, callback):
        self._error_callbacks.append(callback)

    async def publish(self, event_or_envelope, data: msgspec.Struct | None = None,
                      source: str = "", trace_id: str | None = None,
                      user_id: str = "", extra: dict | None = None):
        if isinstance(event_or_envelope, Envelope):
            await self._queue.put(event_or_envelope)
        elif isinstance(event_or_envelope, str) and data:
            await self._queue.put(Envelope.create(
                event_or_envelope, data, source=source, trace_id=trace_id,
                user_id=user_id, extra=extra))
        else:
            raise ValueError("publish expects Envelope or (event_type, struct)")

    async def request(self, event_type: str, data: msgspec.Struct, response_type: str | None = None,
                      source: str = "", timeout: float | None = None,
                      user_id: str = "", extra: dict | None = None) -> Envelope:
        envelope = Envelope.create(event_type, data, source=source, user_id=user_id, extra=extra)
        trace_id = envelope.trace_id
        wait_event = response_type or f"{event_type}.response"
        waiter_key = f"{wait_event}:{trace_id}"
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._waiters[waiter_key] = future
        await self._queue.put(envelope)
        try:
            result = await asyncio.wait_for(future, timeout=timeout or self._default_timeout)
            return result
        except asyncio.TimeoutError:
            return Envelope.error_from(envelope, f"request timeout {timeout or self._default_timeout}s",
                                       code="timeout", source="bus.request")
        finally:
            self._waiters.pop(waiter_key, None)

    def _check_waiters(self, envelope: Envelope):
        key = f"{envelope.event_type}:{envelope.trace_id}"
        future = self._waiters.get(key)
        if future and not future.done():
            future.set_result(envelope)
            return True
        return False

    async def _dispatch(self, envelope: Envelope):
        self.event_log.record(envelope)
        if self._check_waiters(envelope):
            return
        handlers = self._subs.get(envelope.event_type, [])
        if not handlers:
            log.debug(f"no subscribers for '{envelope.event_type}' trace={envelope.trace_id}")
            return
        tasks = [self._run_handler(h, envelope) for h in handlers]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Envelope):
                if not self._check_waiters(result):
                    await self._queue.put(result)

    async def _run_handler(self, handler: Handler, envelope: Envelope) -> Envelope | None:
        name = handler.__class__.__name__
        trace = envelope.trace_id
        t0 = time.time()
        try:
            if handler.execution_mode == "process":
                result = await self._pool.run_task(
                    handler.__class__.__module__,
                    handler.__class__.__name__,
                    envelope
                )
            else:
                result = await asyncio.wait_for(handler.handle(envelope), timeout=handler.timeout)
            dt = round((time.time() - t0) * 1000, 1)
            self.metrics.record(name, dt, True)
            if result:
                self.event_log.record(result, handler_name=name, duration_ms=dt, ok=True)
            log.info(f"{name} ok {dt}ms trace={trace}")
            return result
        except asyncio.TimeoutError:
            dt = round((time.time() - t0) * 1000, 1)
            err = Envelope.error_from(envelope, f"timeout {handler.timeout}s", code="timeout", source=name)
            self.metrics.record(name, dt, False, f"timeout {handler.timeout}s")
            self.event_log.record(err, handler_name=name, duration_ms=dt, ok=False)
            log.error(f"{name} timeout {dt}ms trace={trace}")
            await self._notify_error(err)
            return err
        except Exception as e:
            dt = round((time.time() - t0) * 1000, 1)
            err = Envelope.error_from(envelope, str(e), code="handler_error", source=name)
            self.metrics.record(name, dt, False, str(e))
            self.event_log.record(err, handler_name=name, duration_ms=dt, ok=False)
            log.error(f"{name} error {dt}ms trace={trace} err={e}")
            await self._notify_error(err)
            return err

    async def _run_in_process(self, handler: Handler, envelope: Envelope):
        # Логика перенесена в ProcessPool.run_task
        pass

    async def _notify_error(self, envelope: Envelope):
        for cb in self._error_callbacks:
            try:
                result = cb(envelope)
                if hasattr(result, "__await__"):
                    await result
            except Exception as e:
                log.error(f"error callback failed err={e}")

    async def _run_scheduled(self, task: ScheduledTask):
        while self._running:
            try:
                await asyncio.sleep(task.interval)
                if self._running:
                    await self.publish(task.event_type, task.data, source=task.source)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"scheduled task '{task.event_type}' error err={e}")

    async def start(self):
        self._queue = asyncio.Queue()
        self._waiters = {}
        self._running = True
        self._pool.start()
        for task in self._scheduled:
            task._task = asyncio.create_task(self._run_scheduled(task))
        log.info("bus started")
        while self._running or self._draining:
            try:
                envelope = await asyncio.wait_for(self._queue.get(), timeout=0.5)
                asyncio.create_task(self._dispatch(envelope))
            except asyncio.TimeoutError:
                if self._draining and self._queue.empty():
                    break
                continue
            except Exception as e:
                log.error(f"bus loop error err={e}")

    async def stop(self, drain_timeout: float = 5.0):
        self._running = False
        for task in self._scheduled:
            if task._task:
                task._task.cancel()
                try:
                    await task._task
                except asyncio.CancelledError:
                    pass
        if not self._queue.empty():
            log.info(f"draining {self._queue.qsize()} events")
            self._draining = True
            await asyncio.sleep(min(drain_timeout, 0.1))
            deadline = time.time() + drain_timeout
            while not self._queue.empty() and time.time() < deadline:
                await asyncio.sleep(0.1)
            if not self._queue.empty():
                log.warning(f"drain timeout, {self._queue.qsize()} events lost")
        self._draining = False
        for key, future in self._waiters.items():
            if not future.done():
                future.set_result(Envelope.error_from(
                    Envelope.create("shutdown", msgspec.Struct()),
                    "bus shutting down", code="shutdown", source="bus"))
        self._waiters.clear()
        self._pool.stop()
        log.info("bus stopped")

    def validate_graph(self) -> list[str]:
        warnings = []
        published, subscribed = set(), set()
        for event_type, handlers in self._subs.items():
            subscribed.add(event_type)
            for h in handlers:
                if h.publishes:
                    published.add(h.publishes)
        for d in published - subscribed:
            warnings.append(f"event '{d}' published but no handler subscribes")

        namespaces: dict[str, set] = defaultdict(set)
        for event_type in list(subscribed) + list(published):
            parts = event_type.rsplit(".", 1)
            if len(parts) == 2:
                namespaces[parts[0]].add(event_type)
        return warnings
