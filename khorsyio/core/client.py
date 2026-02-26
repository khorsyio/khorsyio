import logging
import httpx
import msgspec

log = logging.getLogger("khorsyio.httpclient")


class HttpClient:
    def __init__(self):
        self._session: httpx.AsyncClient | None = None

    async def start(self):
        self._session = httpx.AsyncClient()
        log.info("httpclient started")

    async def stop(self):
        if self._session:
            await self._session.aclose()
            log.info("httpclient closed")

    async def get(self, url: str, headers: dict | None = None, timeout: float = 10.0) -> dict:
        assert self._session is not None, "HttpClient is not started"
        resp = await self._session.get(url, headers=headers, timeout=timeout)
        body = resp.text
        return {"status": resp.status_code, "headers": dict(resp.headers), "body": body}

    async def post(self, url: str, data=None, json_data=None, headers: dict | None = None, timeout: float = 10.0) -> dict:
        assert self._session is not None, "HttpClient is not started"
        h = headers.copy() if headers else {}
        content = data
        if json_data is not None:
            content = msgspec.json.encode(json_data)
            h["content-type"] = "application/json"
        resp = await self._session.post(url, content=content, headers=h, timeout=timeout)
        resp_body = resp.text
        return {"status": resp.status_code, "headers": dict(resp.headers), "body": resp_body}
