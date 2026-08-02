"""HTTP middleware: observability (outermost), rate limiting, auth.

Execution order (outer -> inner): observability wraps everything so even
429/401 responses are logged and metered; rate limiting runs before auth
so an unauthenticated flood is throttled cheaply; auth guards the routes.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from .config import Settings
from .services.auth import verify_api_key
from .services.observability import Metrics, log_request
from .services.ratelimit import RateLimiter, resolve_client_ip

_PUBLIC_EXACT = {"/health", "/ready", "/version", "/metrics"}
_PUBLIC_PREFIXES = ("/docs", "/redoc", "/openapi.json")

Handler = Callable[[Request], Awaitable[Response]]


def is_public_path(path: str) -> bool:
    return path in _PUBLIC_EXACT or path.startswith(_PUBLIC_PREFIXES)


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    return getattr(route, "path", "unmatched") if route is not None else "unmatched"


class ObservabilityMiddleware:
    def __init__(self, app: ASGIApp, metrics: Metrics):
        self._app = app
        self._metrics = metrics

    async def __call__(self, scope, receive, send):  # type: ignore[no-untyped-def]
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        request = Request(scope, receive=receive)
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        start = time.perf_counter()
        status_holder = {"status": 500}

        async def send_wrapper(message):  # type: ignore[no-untyped-def]
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = message.setdefault("headers", [])
                headers.append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self._app(scope, receive, send_wrapper)
        finally:
            duration = time.perf_counter() - start
            route = _route_template(request)
            status = status_holder["status"]
            self._metrics.observe(
                request.method, route, status, duration
            )
            log_request(
                {
                    "request_id": request_id,
                    "method": request.method,
                    "route": route,
                    "status": status,
                    "duration_ms": round(duration * 1000, 2),
                    "client_ip": request.client.host if request.client else None,
                }
            )


class RateLimitMiddleware:
    def __init__(
        self, app: ASGIApp, settings: Settings, limiter: RateLimiter
    ):
        self._app = app
        self._settings = settings
        self._limiter = limiter
        self._trusted = frozenset(settings.trusted_proxies)

    async def __call__(self, scope, receive, send):  # type: ignore[no-untyped-def]
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        request = Request(scope, receive=receive)
        if is_public_path(request.url.path):
            await self._app(scope, receive, send)
            return
        peer = request.client.host if request.client else None
        client_ip = resolve_client_ip(
            peer, request.headers.get("x-forwarded-for"), self._trusted
        )
        if not self._limiter.allow(client_ip):
            response = JSONResponse(
                {"detail": "rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": "60"},
            )
            await response(scope, receive, send)
            return
        await self._app(scope, receive, send)


class AuthMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings):
        self._app = app
        self._settings = settings

    async def __call__(self, scope, receive, send):  # type: ignore[no-untyped-def]
        if scope["type"] != "http" or not self._settings.require_api_key:
            await self._app(scope, receive, send)
            return
        request = Request(scope, receive=receive)
        if is_public_path(request.url.path):
            await self._app(scope, receive, send)
            return
        key = request.headers.get("x-api-key")
        if not verify_api_key(key, self._settings.api_keys):
            response = JSONResponse(
                {"detail": "invalid or missing API key"}, status_code=401
            )
            await response(scope, receive, send)
            return
        await self._app(scope, receive, send)
