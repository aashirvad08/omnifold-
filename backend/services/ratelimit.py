"""Per-IP rate limiting with trustworthy client-IP resolution.

The client IP is only taken from ``X-Forwarded-For`` when the *direct
peer* is a configured trusted proxy; otherwise the peer address is used
and any XFF header is ignored. This is what stops a client from spoofing
``X-Forwarded-For`` to dodge or poison another IP's bucket.
"""

from __future__ import annotations

import threading
import time


def resolve_client_ip(
    peer_ip: str | None,
    forwarded_for: str | None,
    trusted_proxies: frozenset[str],
) -> str:
    """The real client IP.

    If the direct peer is not a trusted proxy, XFF is ignored entirely and
    the peer address is returned. If it is trusted, walk the XFF chain from
    the right, skipping further trusted proxies; the first untrusted entry
    is the client. This handles a chain of trusted hops correctly.
    """

    peer = peer_ip or "unknown"
    if peer not in trusted_proxies or not forwarded_for:
        return peer
    chain = [part.strip() for part in forwarded_for.split(",") if part.strip()]
    for candidate in reversed(chain):
        if candidate not in trusted_proxies:
            return candidate
    return chain[0] if chain else peer


class RateLimiter:
    """Fixed-window per-key limiter (in-memory, single-node).

    Not shared across processes; a multi-node deployment would back this
    with Redis. Documented as a single-node limitation.
    """

    def __init__(self, per_minute: int):
        self._per_minute = per_minute
        self._buckets: dict[str, tuple[int, int]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        window = int(time.monotonic() // 60)
        with self._lock:
            start, count = self._buckets.get(key, (window, 0))
            if start != window:
                start, count = window, 0
            if count >= self._per_minute:
                self._buckets[key] = (start, count)
                return False
            self._buckets[key] = (start, count + 1)
            return True

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()
