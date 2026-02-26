import asyncio
import logging
import msgspec
import os
import signal
import importlib
from khorsyio.core.structs import Envelope, Context
from khorsyio.core.handler import Handler

log = logging.getLogger("khorsyio.worker")

class Worker:
    def __init__(self, pipe_conn):
        self.pipe_conn = pipe_conn
        self.loop = None
        self.handlers: dict[str, Handler] = {}

    def run(self):
        # Создаем луп уже внутри нового процесса!
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        # Игнорируем SIGINT в дочерних процессах, чтобы они не падали вместе с главным
        # Но оставляем SIGTERM для завершения по команде Supervisor
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        
        log.info(f"Worker process {os.getpid()} started")
        
        try:
            self.loop.run_until_complete(self._main_loop())
        finally:
            self.loop.close()
            log.info(f"Worker process {os.getpid()} stopped")

    async def _main_loop(self):
        while True:
            try:
                # Читаем данные из Pipe
                if not self.pipe_conn.poll(0.5):
                    await asyncio.sleep(0.01)
                    continue
                
                msg_bytes = self.pipe_conn.recv_bytes()
                # Сначала декодируем обертку как dict, чтобы достать module/class
                # И отдельно Envelope с правильным типом
                raw_data = msgspec.msgpack.decode(msg_bytes)
                
                # Нам нужно передекодировать envelope с правильным типом Envelope
                # Или просто передавать его уже декодированным в task_data
                task_data = {
                    "module": raw_data["module"],
                    "class": raw_data["class"],
                    "envelope": msgspec.msgpack.decode(msgspec.msgpack.encode(raw_data["envelope"]), type=Envelope)
                }
                
                # Обрабатываем сообщение
                result_envelope = await self._process_message(task_data)
                
                # Отправляем результат обратно
                self.pipe_conn.send_bytes(msgspec.msgpack.encode(result_envelope))
            except EOFError:
                break
            except Exception as e:
                log.error(f"Worker loop error: {e}")
                await asyncio.sleep(1)

    async def _process_message(self, task_data: dict) -> Envelope:
        envelope = task_data["envelope"]
        try:
            module_name = task_data["module"]
            class_name = task_data["class"]
            
            # Кэшируем инстансы хендлеров
            handler_key = f"{module_name}:{class_name}"
            if handler_key not in self.handlers:
                mod = importlib.import_module(module_name)
                handler_cls = getattr(mod, class_name)
                self.handlers[handler_key] = handler_cls()
            
            handler = self.handlers[handler_key]
            
            log.info(f"Worker {os.getpid()} starting task {envelope.trace_id}")
            # Выполняем хендлер
            result = await handler.handle(envelope)
            log.info(f"Worker {os.getpid()} finished task {envelope.trace_id}")
            return result
        except Exception as e:
            log.exception(f"Error processing message in worker: {e}")
            return Envelope.error_from(envelope, f"Worker error: {str(e)}", code="worker_error")
