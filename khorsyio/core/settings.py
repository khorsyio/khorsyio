import os
from dotenv import load_dotenv

load_dotenv()


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _int(key: str, default: int = 0) -> int:
    return int(_get(key, str(default)))


def _bool(key: str, default: bool = False) -> bool:
    return _get(key, str(default)).lower() in ("1", "true", "yes")


class ServerSettings:
    host: str = _get("SERVER_HOST", "0.0.0.0")
    port: int = _int("SERVER_PORT", 8000)
    debug: bool = _bool("SERVER_DEBUG", False)
    workers: int = _int("SERVER_WORKERS", 1)


class DbSettings:
    dsn: str = _get("DB_DSN", "postgresql+asyncpg://localhost:5432/khorsyio")
    pool_min: int = _int("DB_POOL_MIN", 2)
    pool_max: int = _int("DB_POOL_MAX", 10)


class BusSettings:
    handler_timeout: float = float(_get("BUS_HANDLER_TIMEOUT", "30.0"))


class Settings:
    server = ServerSettings()
    db = DbSettings()
    bus = BusSettings()


settings = Settings()
