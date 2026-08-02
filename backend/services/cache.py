"""Tiered result cache.

Three tiers by data mutability:

- ``IMMUTABLE`` — results over published data, keyed by the content
  SHA-256; long TTL, safe to keep because the inputs never change.
- ``SESSION`` — results over uploaded data, keyed by the upload checksum;
  short TTL, evicted with the upload.
- ``NONE`` — never cached (e.g. job status).

The cache **key** folds in every input that changes the output — the
operation type (so histogram / comparison / envelope can never collide),
the content checksum, observable, variation, and the *type-tagged* bins
token so ``None`` vs ``nbins:30`` vs explicit edges are always distinct.

The backend is swappable: in-memory (dev/single-node) or Redis (prod).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any


class CacheTier(StrEnum):
    IMMUTABLE = "immutable"
    SESSION = "session"
    NONE = "none"


def bins_token(bins: list[float] | int | None) -> str:
    """Type-tagged token so bin specifications can never collide.

    ``None`` resolves differently per operation/observable, ``int`` is an
    auto-binned count, and a list is explicit edges — three different
    outputs that must map to three different tokens. ``repr(float(...))``
    keeps edge values exact in the key.
    """

    if bins is None:
        return "none"
    if isinstance(bins, bool):  # guard: bool is an int subclass
        raise TypeError("bins must not be a bool")
    if isinstance(bins, int):
        return f"nbins:{bins}"
    return "edges:" + ",".join(repr(float(edge)) for edge in bins)


def make_cache_key(
    operation: str,
    content_checksum: str | None,
    observable: str,
    bins: list[float] | int | None,
    variation: str | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    payload = {
        "op": operation,
        "content": content_checksum,
        "observable": observable,
        "bins": bins_token(bins),
        "variation": variation,
        "extra": extra or {},
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class CacheBackend(ABC):
    @abstractmethod
    def get(self, key: str) -> Any | None: ...

    @abstractmethod
    def set(self, key: str, value: Any, ttl_seconds: int) -> None: ...

    @abstractmethod
    def clear(self) -> None: ...


class MemoryCacheBackend(CacheBackend):
    """Thread-safe in-memory cache with TTL and simple LRU eviction."""

    def __init__(self, max_entries: int = 2048):
        self._max = max_entries
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        now = time.monotonic()
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            expiry, value = item
            if expiry < now:
                del self._store[key]
                return None
            # refresh recency
            del self._store[key]
            self._store[key] = (expiry, value)
            return value

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        expiry = time.monotonic() + ttl_seconds
        with self._lock:
            if key in self._store:
                del self._store[key]
            self._store[key] = (expiry, value)
            while len(self._store) > self._max:
                oldest = next(iter(self._store))
                del self._store[oldest]

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


class RedisCacheBackend(CacheBackend):
    """Redis-backed cache for production (values stored as JSON).

    Requires ``redis``; construction fails clearly if it is missing so a
    misconfigured prod deploy surfaces at startup rather than silently
    running without a shared cache.
    """

    def __init__(self, url: str, namespace: str = "omnifold"):
        try:
            import redis
        except ImportError as exc:  # pragma: no cover - env dependent
            raise RuntimeError(
                "cache_backend='redis' requires the 'redis' package."
            ) from exc
        self._client = redis.Redis.from_url(url)
        self._namespace = namespace

    def _k(self, key: str) -> str:
        return f"{self._namespace}:{key}"

    def get(self, key: str) -> Any | None:
        raw = self._client.get(self._k(key))
        return None if raw is None else json.loads(raw)

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        self._client.set(self._k(key), json.dumps(value), ex=ttl_seconds)

    def clear(self) -> None:  # pragma: no cover - prod only
        for found in self._client.scan_iter(f"{self._namespace}:*"):
            self._client.delete(found)


class ResultCache:
    """Applies the right TTL per tier over a swappable backend."""

    def __init__(
        self,
        backend: CacheBackend,
        immutable_ttl: int,
        session_ttl: int,
    ):
        self._backend = backend
        self._ttls = {
            CacheTier.IMMUTABLE: immutable_ttl,
            CacheTier.SESSION: session_ttl,
        }

    def get(self, tier: CacheTier, key: str) -> Any | None:
        if tier is CacheTier.NONE:
            return None
        return self._backend.get(key)

    def set(self, tier: CacheTier, key: str, value: Any) -> None:
        if tier is CacheTier.NONE:
            return
        self._backend.set(key, value, self._ttls[tier])

    def clear(self) -> None:
        self._backend.clear()


def build_cache(
    backend_kind: str,
    redis_url: str | None,
    immutable_ttl: int,
    session_ttl: int,
    max_entries: int,
) -> ResultCache:
    if backend_kind == "redis":
        if not redis_url:
            raise RuntimeError("cache_backend='redis' requires redis_url.")
        backend: CacheBackend = RedisCacheBackend(redis_url)
    else:
        backend = MemoryCacheBackend(max_entries=max_entries)
    return ResultCache(backend, immutable_ttl, session_ttl)
