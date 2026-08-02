"""Request IDs, structured logging, and Prometheus metrics."""

from __future__ import annotations

import json
import logging

from backend.services.observability import ACCESS_LOGGER, log_request


def test_request_id_generated_and_echoed(client):
    resp = client.get("/health")
    generated = resp.headers.get("x-request-id")
    assert generated and len(generated) == 32

    # an incoming request id is preserved
    resp2 = client.get("/health", headers={"X-Request-ID": "trace-abc"})
    assert resp2.headers["x-request-id"] == "trace-abc"


def test_metrics_use_route_template_not_raw_path(client):
    client.get("/packages/zjets_nominal/histogram?observable=pT_ll")
    client.get("/packages/zjets_sherpa/histogram?observable=pT_ll")
    body = client.get("/metrics").content.decode()
    # both distinct resource ids collapse to one route-template series
    assert 'route="/packages/{resource_id}/histogram"' in body
    # the raw ids never appear as labels (no cardinality explosion)
    assert "zjets_nominal" not in body
    assert "zjets_sherpa" not in body


def test_unmatched_paths_do_not_explode_cardinality(client):
    client.get("/no/such/route/aaa")
    client.get("/no/such/route/bbb")
    body = client.get("/metrics").content.decode()
    assert 'route="unmatched"' in body
    assert "aaa" not in body and "bbb" not in body


def test_request_count_increments(client):
    before = client.get("/metrics").content.decode()
    client.get("/resources")
    client.get("/resources")
    after = client.get("/metrics").content.decode()
    assert 'route="/resources"' in after
    # the counter series exists after traffic (was absent or lower before)
    assert before != after


def test_log_request_emits_structured_json():
    logger = logging.getLogger(ACCESS_LOGGER)
    records: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(self.format(record))

    from backend.services.observability import _JsonFormatter

    handler = _Capture()
    handler.setFormatter(_JsonFormatter())
    logger.addHandler(handler)
    previous_level = logger.level
    logger.setLevel(logging.INFO)  # the quiet-log fixture raises it otherwise
    try:
        log_request(
            {"request_id": "abc", "method": "GET", "route": "/x", "status": 200}
        )
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    assert records, "no log emitted"
    payload = json.loads(records[-1])
    assert payload["request_id"] == "abc"
    assert payload["route"] == "/x"
    assert payload["status"] == 200
