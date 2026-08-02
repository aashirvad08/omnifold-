"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import API_VERSION
from .config import Settings, get_settings
from .middleware import (
    AuthMiddleware,
    ObservabilityMiddleware,
    RateLimitMiddleware,
)
from .routes import (
    exports,
    health,
    histograms,
    jobs,
    resources,
    sources,
    uploads,
)
from .services.cache import build_cache
from .services.jobs import JobManager
from .services.observability import Metrics, setup_logging
from .services.ratelimit import RateLimiter
from .services.registry import Registry


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        app.state.jobs.shutdown()

    app = FastAPI(
        title="OmniFold Publication Explorer API",
        version=API_VERSION,
        summary="Read and compute over published OmniFold measurements.",
        lifespan=lifespan,
    )

    app.state.settings = settings
    app.state.registry = Registry(settings.data_root)
    app.state.cache = build_cache(
        backend_kind=settings.cache_backend,
        redis_url=settings.redis_url,
        immutable_ttl=settings.immutable_cache_ttl_seconds,
        session_ttl=settings.session_cache_ttl_seconds,
        max_entries=settings.cache_max_entries,
    )
    app.state.jobs = JobManager(
        retention_seconds=settings.job_retention_seconds
    )
    setup_logging()
    app.state.metrics = Metrics()
    app.state.rate_limiter = RateLimiter(settings.rate_limit_per_minute)

    app.include_router(health.router)
    app.include_router(sources.router)
    app.include_router(resources.router)
    app.include_router(histograms.router)
    app.include_router(exports.router)
    app.include_router(uploads.router)
    app.include_router(jobs.router)

    # add inner-first: auth, then rate limit, then observability outermost
    app.add_middleware(AuthMiddleware, settings=settings)
    app.add_middleware(
        RateLimitMiddleware, settings=settings, limiter=app.state.rate_limiter
    )
    app.add_middleware(ObservabilityMiddleware, metrics=app.state.metrics)
    return app
