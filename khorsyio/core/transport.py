import logging
import msgspec
import socketio
from khorsyio.core.structs import Envelope, Context
from khorsyio.core.bus import Bus

log = logging.getLogger("khorsyio.transport")


class SocketTransport:
    def __init__(self, bus: Bus, async_mode: str = "asgi"):
        self.bus = bus
        self.sio = socketio.AsyncServer(async_mode=async_mode, cors_allowed_origins="*")
        self.asgi_app = socketio.ASGIApp(self.sio)
        self._sid_map: dict[str, dict] = {}
        self._setup()

    def _setup(self):
        @self.sio.event
        async def connect(sid, environ):
            self._sid_map[sid] = {}
            log.info(f"ws connect sid={sid}")

        @self.sio.event
        async def disconnect(sid):
            self._sid_map.pop(sid, None)
            log.info(f"ws disconnect sid={sid}")

        @self.sio.event
        async def event(sid, data):
            try:
                event_type = data.get("event_type")
                payload = data.get("payload", {})
                trace_id = data.get("trace_id")
                user_id = data.get("user_id", "")
                if not event_type:
                    await self.sio.emit("error", {"error": "missing event_type"}, to=sid)
                    return
                ctx = Context(source=f"ws:{sid}", user_id=user_id,
                              extra={"_ws_sid": sid, "_ws_reply": data.get("reply_event", "")})
                if trace_id:
                    ctx.trace_id = trace_id
                envelope = Envelope(ctx=ctx, event_type=event_type, payload=msgspec.json.encode(payload))
                log.info(f"ws event type={event_type} sid={sid} trace={ctx.trace_id}")
                await self.bus.publish(envelope)
            except Exception as e:
                log.error(f"ws event error sid={sid} err={e}")
                await self.sio.emit("error", {"error": str(e)}, to=sid)

    async def reply_to_sender(self, envelope: Envelope):
        sid = envelope.ctx.extra.get("_ws_sid")
        if sid and sid in self._sid_map:
            await self.emit_envelope(envelope, sid=sid)
            return True
        return False

    async def emit(self, event_name: str, data, sid: str | None = None):
        await self.sio.emit(event_name, data, to=sid) if sid else await self.sio.emit(event_name, data)

    async def emit_envelope(self, envelope: Envelope, sid: str | None = None):
        data = {
            "event_type": envelope.event_type,
            "payload": msgspec.json.decode(envelope.payload),
            "trace_id": envelope.trace_id,
            "error": {"code": envelope.error.code, "message": envelope.error.message} if envelope.error else None,
        }
        await self.emit("event", data, sid)

    @property
    def connected_sids(self) -> list[str]:
        return list(self._sid_map.keys())


class SocketClient:
    def __init__(self, url: str):
        self.url = url
        self.sio = socketio.AsyncClient()
        self._handlers: dict[str, list] = {}

    def on(self, event_type: str, callback):
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(callback)

    async def connect(self):
        @self.sio.event
        async def event(data):
            et = data.get("event_type", "")
            for cb in self._handlers.get(et, []):
                result = cb(data)
                if hasattr(result, "__await__"):
                    await result

        @self.sio.event
        async def error(data):
            log.error(f"client received error: {data}")

        await self.sio.connect(self.url)
        log.info(f"client connected to {self.url}")

    async def send(self, event_type: str, payload: dict, trace_id: str | None = None):
        data = {"event_type": event_type, "payload": payload}
        if trace_id:
            data["trace_id"] = trace_id
        await self.sio.emit("event", data)

    async def disconnect(self):
        await self.sio.disconnect()
