"""FastAPI dependency accessors for shared services on app.state."""

from __future__ import annotations

from fastapi import Request

from .config import Settings
from .services.cache import ResultCache
from .services.jobs import JobManager
from .services.registry import Registry


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_registry(request: Request) -> Registry:
    registry: Registry = request.app.state.registry
    return registry


def get_cache(request: Request) -> ResultCache:
    cache: ResultCache = request.app.state.cache
    return cache


def get_jobs(request: Request) -> JobManager:
    jobs: JobManager = request.app.state.jobs
    return jobs
