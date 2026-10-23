import asyncio
import time

import httpx
import jwt
import pytest

from authentik_sso import services
from authentik_sso.services import ServiceTokenError, ServiceTokenProvider

pytestmark = pytest.mark.anyio

TOKEN_URL = "http://authentik.local/application/o/token/"


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


class TokenEndpoint:
    """Fake Authentik token endpoint: issues tokens valid for `ttl` seconds from `clock`."""

    def __init__(self, clock, ttl=300, status=200):
        self.clock, self.ttl, self.status = clock, ttl, status
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "invalid_client"})
        token = jwt.encode(
            {"n": len(self.calls), "exp": int(self.clock()) + self.ttl}, "k" * 32, algorithm="HS256"
        )
        return httpx.Response(200, json={"access_token": token, "expires_in": self.ttl})


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def endpoint(clock):
    return TokenEndpoint(clock)


@pytest.fixture
def provider(endpoint, clock):
    return ServiceTokenProvider(
        TOKEN_URL, "accounting-id", "accounting-secret", transport=httpx.MockTransport(endpoint), clock=clock
    )


async def test_token_fetched_with_client_credentials(provider, endpoint):
    await provider.get_token()
    (request,) = endpoint.calls
    assert str(request.url) == TOKEN_URL
    body = dict(httpx.QueryParams(request.content.decode()))
    assert body == {
        "grant_type": "client_credentials",
        "client_id": "accounting-id",
        "client_secret": "accounting-secret",
    }


async def test_token_cached_until_exp_minus_30s(provider, endpoint, clock):
    first = await provider.get_token()
    clock.now += 300 - 31
    assert await provider.get_token() == first
    assert len(endpoint.calls) == 1

    clock.now += 2  # now inside the 30s margin
    assert await provider.get_token() != first
    assert len(endpoint.calls) == 2


async def test_concurrent_callers_share_one_fetch(provider, endpoint):
    tokens = await asyncio.gather(*(provider.get_token() for _ in range(10)))
    assert len(set(tokens)) == 1
    assert len(endpoint.calls) == 1


async def test_expires_in_used_when_token_has_no_exp(clock):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"access_token": f"opaque-{len(calls)}", "expires_in": 120})

    provider = ServiceTokenProvider(TOKEN_URL, "id", "s", transport=httpx.MockTransport(handler), clock=clock)
    assert await provider.get_token() == "opaque-1"
    clock.now += 89
    assert await provider.get_token() == "opaque-1"
    clock.now += 2
    assert await provider.get_token() == "opaque-2"


async def test_token_endpoint_error(endpoint, provider):
    endpoint.status = 401
    with pytest.raises(ServiceTokenError) as exc:
        await provider.get_token()
    assert exc.value.status_code == 401


async def test_token_endpoint_unreachable(clock):
    def handler(request):
        raise httpx.ConnectError("refused")

    provider = ServiceTokenProvider(TOKEN_URL, "id", "s", transport=httpx.MockTransport(handler), clock=clock)
    with pytest.raises(ServiceTokenError) as exc:
        await provider.get_token()
    assert exc.value.status_code is None


def test_token_url_from_issuer():
    assert (
        services.token_url_from_issuer("http://authentik.local/application/o/accounting/")
        == "http://authentik.local/application/o/token/"
    )


def test_service_url(monkeypatch):
    monkeypatch.setenv("SERVICE_DOMAIN", "develop")
    assert services.service_url("registry") == "http://api.registry.develop"
    monkeypatch.setenv("SERVICE_URL_AVTO_LOGISTIC", "http://127.0.0.1:8001/")
    assert services.service_url("avto.logistic") == "http://127.0.0.1:8001"


def test_service_url_needs_domain(monkeypatch):
    monkeypatch.delenv("SERVICE_DOMAIN", raising=False)
    with pytest.raises(RuntimeError):
        services.service_url("registry")


class Target:
    """Fake callee: records requests, answers 401 to the first `reject` tokens."""

    def __init__(self, reject=0):
        self.reject = reject
        self.requests: list[httpx.Request] = []
        self.tokens: list[str] = []  # snapshot: a retried request object is the same instance

    def __call__(self, request):
        self.requests.append(request)
        self.tokens.append(request.headers["Authorization"])
        if self.reject:
            self.reject -= 1
            return httpx.Response(401)
        return httpx.Response(200, json={"ok": True})


async def test_client_sends_token_and_headers(monkeypatch, provider):
    monkeypatch.setenv("SERVICE_DOMAIN", "local")
    target = Target()
    async with services.client(
        "registry", on_behalf_of="user-sub", token_provider=provider, transport=httpx.MockTransport(target)
    ) as registry:
        await registry.put("/internal/v1/pool/by-source/accounting/1", json={})
        await registry.get("/internal/v1/pool/by-source/accounting/1")

    first, second = target.requests
    assert str(first.url) == "http://api.registry.local/internal/v1/pool/by-source/accounting/1"
    assert first.headers["Authorization"] == f"Bearer {await provider.get_token()}"
    assert first.headers["X-On-Behalf-Of"] == "user-sub"
    assert first.headers["X-Request-ID"] and first.headers["X-Request-ID"] == second.headers["X-Request-ID"]


async def test_client_request_id_passed_through_and_no_on_behalf_of(monkeypatch, provider):
    monkeypatch.setenv("SERVICE_DOMAIN", "local")
    target = Target()
    async with services.client(
        "registry", request_id="req-1", token_provider=provider, transport=httpx.MockTransport(target)
    ) as registry:
        await registry.get("/x")
    (request,) = target.requests
    assert request.headers["X-Request-ID"] == "req-1"
    assert "X-On-Behalf-Of" not in request.headers


async def test_client_refetches_token_once_on_401(monkeypatch, provider, endpoint):
    monkeypatch.setenv("SERVICE_DOMAIN", "local")
    target = Target(reject=1)
    async with services.client("registry", token_provider=provider, transport=httpx.MockTransport(target)) as c:
        r = await c.put("/x", json={"a": 1})
    assert r.status_code == 200
    assert len(endpoint.calls) == 2
    stale, fresh = target.tokens
    assert stale != fresh
    assert target.requests[1].content == b'{"a":1}'


async def test_client_gives_up_after_second_401(monkeypatch, provider, endpoint):
    monkeypatch.setenv("SERVICE_DOMAIN", "local")
    target = Target(reject=5)
    async with services.client("registry", token_provider=provider, transport=httpx.MockTransport(target)) as c:
        r = await c.get("/x")
    assert r.status_code == 401
    assert len(target.requests) == 2


async def test_default_provider_from_env(monkeypatch):
    monkeypatch.setattr(services, "_default_provider", None)
    monkeypatch.setenv("AUTHENTIK_ISSUER", "http://authentik.local/application/o/accounting/")
    monkeypatch.setenv("SERVICE_CLIENT_ID", "id")
    monkeypatch.setenv("SERVICE_CLIENT_SECRET", "s")
    provider = services.default_token_provider()
    assert provider.token_url == "http://authentik.local/application/o/token/"
    assert services.default_token_provider() is provider
