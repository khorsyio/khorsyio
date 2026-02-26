import asyncio
import logging
from khorsyio.core.bus import Bus
from khorsyio.core.http import Router, HttpApp, CorsConfig, Response
from khorsyio.core.transport import SocketTransport
from khorsyio.core.client import HttpClient
from khorsyio.core.settings import settings
from khorsyio.db.database import Database

log = logging.getLogger("khorsyio.app")


class App:
    def __init__(self, cors: CorsConfig | None = None):
        self.bus = Bus(handler_timeout=settings.bus.handler_timeout)
        self.router = Router()
        self.cors = cors
        self.transport = SocketTransport(self.bus)
        self.db = Database()
        self.client = HttpClient()
        self._http = HttpApp(self.router, cors=self.cors)
        self._bus_task: asyncio.Task | None = None

    def register(self, handler):
        self.bus.register(handler)

    def mount(self, domain):
        domain.setup(self)

    async def startup(self):
        try:
            await self.db.connect()
        except Exception as e:
            log.warning(f"db connection skipped err={e}")
        await self.client.start()
        warnings = self.bus.validate_graph()
        for w in warnings:
            log.warning(f"graph: {w}")
        self._bus_task = asyncio.create_task(self.bus.start())
        log.info(f"app started host={settings.server.host} port={settings.server.port}")

    async def shutdown(self):
        await self.bus.stop(drain_timeout=5.0)
        if self._bus_task:
            self._bus_task.cancel()
            try:
                await self._bus_task
            except asyncio.CancelledError:
                pass
        await self.client.stop()
        await self.db.close()
        log.info("app shutdown")

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                msg = await receive()
                if msg["type"] == "lifespan.startup":
                    await self.startup()
                    await send({"type": "lifespan.startup.complete"})
                elif msg["type"] == "lifespan.shutdown":
                    await self.shutdown()
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        elif scope["type"] == "http":
            await self._http(scope, receive, send)
        elif scope["type"] == "websocket":
            await self.transport.asgi_app(scope, receive, send)

    def run(self, target: str | None = None):
        import os
        import sys

        if target is None:
            import __main__
            if hasattr(__main__, '__file__'):
                script_path = os.path.abspath(__main__.__file__)
                script_dir = os.path.dirname(script_path)
                module_name = os.path.splitext(os.path.basename(script_path))[0]
                target = f"{module_name}:app"

                current_pythonpath = os.environ.get("PYTHONPATH", "")
                if script_dir not in current_pythonpath.split(os.pathsep):
                    os.environ["PYTHONPATH"] = f"{script_dir}{os.pathsep}{current_pythonpath}".strip(os.pathsep)
            else:
                target = "app:app"

        try:
            from granian import Granian
            Granian(target, address=settings.server.host, port=settings.server.port, interface="asgi").serve()
        except ImportError:
            import uvicorn
            uvicorn.run(self, host=settings.server.host, port=settings.server.port)
