"""Structured logging and Prometheus metrics.

Metrics live on a **per-app** registry (not the global default) so that
constructing several apps — as tests do — never double-registers a
collector. Labels use the matched **route template**
(``/packages/{resource_id}/histogram``), never the raw path, so an
attacker cannot explode label cardinality with arbitrary URLs.
"""

from __future__ import annotations

import json
import logging
import sys

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

ACCESS_LOGGER = "omnifold.access"


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "context", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return json.dumps(payload)


def setup_logging(level: int = logging.INFO) -> None:
    logger = logging.getLogger(ACCESS_LOGGER)
    if logger.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False


def log_request(context: dict[str, object]) -> None:
    logging.getLogger(ACCESS_LOGGER).info(
        "request", extra={"context": context}
    )


class Metrics:
    """Per-app Prometheus collectors."""

    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "http_requests_total",
            "HTTP requests by method, route template and status.",
            ["method", "route", "status"],
            registry=self.registry,
        )
        self.latency = Histogram(
            "http_request_duration_seconds",
            "HTTP request latency by method and route template.",
            ["method", "route"],
            registry=self.registry,
        )

    def observe(
        self, method: str, route: str, status: int, duration: float
    ) -> None:
        self.requests.labels(method, route, str(status)).inc()
        self.latency.labels(method, route).observe(duration)

    def render(self) -> bytes:
        return generate_latest(self.registry)
