"""Rate limiting (per-IP + XFF trust), SSRF egress, and API-key auth."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings
from backend.services.auth import EgressDenied, ensure_source_allowed, verify_api_key
from backend.services.ratelimit import RateLimiter, resolve_client_ip


def _peer_client(app, ip: str) -> TestClient:
    # TestClient's `client` sets scope["client"], i.e. a real distinct peer
    return TestClient(app, client=(ip, 0))


# --- client-IP resolution (the core of XFF trust) ------------------------


def test_untrusted_peer_ignores_forwarded_for():
    trusted: frozenset[str] = frozenset({"10.0.0.1"})
    # peer is not a trusted proxy -> XFF is ignored, spoof has no effect
    assert resolve_client_ip("9.9.9.9", "1.2.3.4", trusted) == "9.9.9.9"
    assert resolve_client_ip("9.9.9.9", "5.6.7.8", trusted) == "9.9.9.9"


def test_trusted_peer_uses_rightmost_untrusted_hop():
    trusted = frozenset({"10.0.0.1", "10.0.0.2"})
    # chain: real client, then two trusted proxies
    assert (
        resolve_client_ip("10.0.0.1", "1.1.1.1, 10.0.0.2", trusted) == "1.1.1.1"
    )
    assert resolve_client_ip("10.0.0.1", "1.1.1.1", trusted) == "1.1.1.1"


# --- rate limiter unit ---------------------------------------------------


def test_rate_limiter_is_per_key():
    limiter = RateLimiter(per_minute=2)
    assert limiter.allow("a") and limiter.allow("a")
    assert not limiter.allow("a")  # a exhausted
    assert limiter.allow("b")  # b independent


# --- rate limiting through the full stack, real distinct peers -----------


def test_rate_limit_is_per_ip_two_distinct_peers(data_root):
    app = create_app(
        Settings(env="dev", data_root=data_root, rate_limit_per_minute=3)
    )
    peer1 = _peer_client(app, "1.1.1.1")
    peer2 = _peer_client(app, "2.2.2.2")
    for _ in range(3):
        assert peer1.get("/resources").status_code == 200
    assert peer1.get("/resources").status_code == 429  # peer1 exhausted
    assert peer2.get("/resources").status_code == 200  # peer2 independent


def test_spoofed_forwarded_for_does_not_move_the_bucket(data_root):
    app = create_app(
        Settings(env="dev", data_root=data_root, rate_limit_per_minute=3)
    )
    # untrusted peer (no trusted_proxies configured) varies XFF each request
    peer = _peer_client(app, "9.9.9.9")
    for i in range(3):
        resp = peer.get(
            "/resources", headers={"X-Forwarded-For": f"1.2.3.{i}"}
        )
        assert resp.status_code == 200
    # all counted against 9.9.9.9 regardless of the spoofed header
    assert (
        peer.get(
            "/resources", headers={"X-Forwarded-For": "5.5.5.5"}
        ).status_code
        == 429
    )


def test_trusted_proxy_buckets_per_forwarded_client(data_root):
    app = create_app(
        Settings(
            env="dev",
            data_root=data_root,
            rate_limit_per_minute=3,
            trusted_proxies=("10.0.0.1",),
        )
    )
    proxy = _peer_client(app, "10.0.0.1")  # a trusted proxy
    for _ in range(3):
        assert (
            proxy.get(
                "/resources", headers={"X-Forwarded-For": "1.1.1.1"}
            ).status_code
            == 200
        )
    # client A exhausted, client B (distinct XFF) still fine
    assert (
        proxy.get(
            "/resources", headers={"X-Forwarded-For": "1.1.1.1"}
        ).status_code
        == 429
    )
    assert (
        proxy.get(
            "/resources", headers={"X-Forwarded-For": "2.2.2.2"}
        ).status_code
        == 200
    )


# --- SSRF egress ---------------------------------------------------------


class _Egress:
    def __init__(self, allowed, allowlist):
        self.allowed_source_kinds = allowed
        self.egress_allowlist = allowlist


def test_url_and_s3_are_denied_by_default():
    settings = _Egress(("local", "upload", "zenodo"), ("zenodo.org",))
    for spec in ("url:https://evil.example/x.parquet", "s3://bucket/key"):
        with pytest.raises(EgressDenied):
            ensure_source_allowed(spec, settings)


def test_local_upload_zenodo_allowed_and_zenodo_host_pinned():
    settings = _Egress(("local", "upload", "zenodo"), ("zenodo.org",))
    ensure_source_allowed("local:artifacts/zjets", settings)
    ensure_source_allowed("upload:staged.parquet", settings)
    ensure_source_allowed("zenodo:11507450", settings)  # host-pinned, allowed
    # zenodo not on the allowlist -> denied even though the kind is enabled
    strict = _Egress(("zenodo",), ("example.org",))
    with pytest.raises(EgressDenied):
        ensure_source_allowed("zenodo:11507450", strict)


def test_url_enabled_still_requires_allowlisted_host():
    settings = _Egress(("local", "url"), ("data.example.org",))
    with pytest.raises(EgressDenied):
        ensure_source_allowed("url:https://evil.example/x.parquet", settings)
    ensure_source_allowed("url:https://data.example.org/x.parquet", settings)


def test_sources_policy_endpoint(client):
    body = client.get("/sources").json()
    kinds = {k["kind"]: k for k in body["kinds"]}
    assert kinds["url"]["allowed"] is False
    assert kinds["s3"]["allowed"] is False
    assert kinds["zenodo"]["allowed"] is True
    assert "zenodo.org" in body["egress_allowlist"]


# --- API-key auth --------------------------------------------------------


def test_verify_api_key_constant_time_membership():
    keys = ("alpha", "bravo")
    assert verify_api_key("bravo", keys) is True
    assert verify_api_key("charlie", keys) is False
    assert verify_api_key(None, keys) is False


def test_auth_required_in_prod_optional_in_dev(data_root):
    # dev: no key needed
    dev = create_app(Settings(env="dev", data_root=data_root))
    assert TestClient(dev).get("/resources").status_code == 200

    secured = create_app(
        Settings(
            env="dev",
            data_root=data_root,
            require_api_key=True,
            api_keys=("s3cret",),
        )
    )
    c = TestClient(secured)
    assert c.get("/resources").status_code == 401  # missing key
    assert c.get(
        "/resources", headers={"X-API-Key": "wrong"}
    ).status_code == 401
    assert c.get(
        "/resources", headers={"X-API-Key": "s3cret"}
    ).status_code == 200
    # public paths remain reachable without a key
    assert c.get("/health").status_code == 200


def test_prod_environment_forces_auth(data_root):
    prod = Settings(env="prod", data_root=data_root).apply_environment_defaults()
    assert prod.require_api_key is True
