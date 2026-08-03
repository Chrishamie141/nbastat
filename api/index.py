"""Vercel ASGI entrypoint for the SmartBetSports backend project.

Vercel's FastAPI runtime currently exposes an internal rewrite destination as
the ASGI path.  The rewrite stores the public path in ``__path`` so this small
adapter can restore it before FastAPI routes the request.
"""

from urllib.parse import parse_qsl, urlencode

from backend.app.main import app as fastapi_app


async def app(scope, receive, send):
    if scope.get("type") != "http":
        await fastapi_app(scope, receive, send)
        return

    query = parse_qsl(scope.get("query_string", b"").decode("utf-8"), keep_blank_values=True)
    original_path = next((value for key, value in query if key == "__path"), "")
    forwarded_query = [(key, value) for key, value in query if key != "__path"]
    public_path = "/" + original_path.lstrip("/")
    forwarded_scope = {
        **scope,
        "path": public_path,
        "raw_path": public_path.encode("utf-8"),
        "query_string": urlencode(forwarded_query, doseq=True).encode("utf-8"),
    }
    await fastapi_app(forwarded_scope, receive, send)


__all__ = ["app"]
