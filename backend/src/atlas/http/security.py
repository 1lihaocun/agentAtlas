from urllib.parse import urlsplit

from starlette.datastructures import Headers, QueryParams
from starlette.responses import JSONResponse


class LocalAccess:
    def __init__(self, app, config):
        self.app = app
        self.config = config

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        origin = headers.get("origin")
        try:
            host = urlsplit("http://" + headers.get("host", ""))
            permitted = host.hostname in {"localhost", "127.0.0.1", "::1"} and host.port == self.config.port
            if origin and origin != "http://" + host.netloc and origin not in self.config.allowed_origins:
                permitted = False
        except ValueError:
            permitted = False
        if headers.get("sec-fetch-site") == "cross-site":
            permitted = False
        if not permitted:
            return await self.reject(scope, receive, send, "仅允许已配置的本机同源请求", 403)
        params = QueryParams(scope["query_string"])
        if len(params.multi_items()) != len(params):
            return await self.reject(scope, receive, send, "查询参数重复", 400)
        if scope["method"] in {"POST", "PUT", "PATCH"}:
            media_type = headers.get("content-type", "").split(";")[0].strip().lower()
            optional_body = scope["path"] in {"/api/rescan", "/api/assets/rescan"}
            if media_type != "application/json" and not (optional_body and not media_type):
                return await self.reject(scope, receive, send, "请求需要使用 JSON", 415)
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > 4 * 1024 * 1024:
                    return await self.reject(scope, receive, send, "请求内容超过 4 MiB", 413)
                if not message.get("more_body", False):
                    break
            if body and media_type != "application/json":
                return await self.reject(scope, receive, send, "请求需要使用 JSON", 415)
            delivered = False

            async def body_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            return await self.app(scope, body_receive, send)
        return await self.app(scope, receive, send)

    @staticmethod
    async def reject(scope, receive, send, error, status):
        return await JSONResponse({"ok": False, "error": error}, status_code=status)(scope, receive, send)
