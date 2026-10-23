"""Calling other services through the gateway, authenticated as this service.

Entry point is `AuthentikAuth.service_client(name)`; settings come from `SSOConfig`:

    base URL  http://api.registry.{service_domain}   (service_urls["REGISTRY"] overrides)
    token     client_credentials of this service's `<svc>-m2m` provider, cached until exp - 30s
"""

import asyncio
import re
import time
import uuid
from collections.abc import Callable

import httpx
import jwt

from .config import SSOConfig
from .m2m import ON_BEHALF_OF_HEADER

REQUEST_ID_HEADER = "X-Request-ID"
TOKEN_LEEWAY_SECONDS = 30


class ServiceTokenError(RuntimeError):
    """Token endpoint failed; `status_code` is None for network errors."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def token_url_from_issuer(issuer: str) -> str:
    """'http://authentik.local/application/o/accounting/' -> 'http://authentik.local/application/o/token/'."""
    base_url = issuer.split("/o/")[0]
    return f"{base_url}/o/token/"


def env_key(name: str) -> str:
    """'avto.logistic' -> 'AVTO_LOGISTIC' (suffix of SERVICE_URL_<NAME>)."""
    return re.sub(r"[^A-Za-z0-9]", "_", name).upper()


def service_url(config: SSOConfig, name: str) -> str:
    """'registry' -> SERVICE_URL_REGISTRY or 'http://api.registry.{SERVICE_DOMAIN}'."""
    overrides = {env_key(key): url for key, url in config.service_urls.items()}
    if override := overrides.get(env_key(name)):
        return override.rstrip("/")
    if not config.service_domain:
        raise RuntimeError(f"SERVICE_DOMAIN is not set (needed to reach service {name!r})")
    return f"http://api.{name}.{config.service_domain}"


class ServiceTokenProvider:
    """This service's m2m access token, fetched once and reused until `exp - 30s`.

    Concurrent callers share one fetch (asyncio.Lock); `invalidate()` forces a refetch.
    """

    def __init__(
        self,
        token_url: str,
        client_id: str,
        client_secret: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._transport = transport
        self._clock = clock
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    def _cached(self) -> str | None:
        if self._token and self._clock() < self._expires_at - TOKEN_LEEWAY_SECONDS:
            return self._token
        return None

    async def get_token(self) -> str:
        if token := self._cached():
            return token
        async with self._lock:
            if token := self._cached():
                return token
            return await self._fetch()

    def invalidate(self, token: str) -> None:
        if self._token == token:
            self._token = None

    async def _fetch(self) -> str:
        now = self._clock()
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=10.0) as http:
                response = await http.post(
                    self.token_url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                    },
                )
        except httpx.HTTPError as exc:
            raise ServiceTokenError(f"Token endpoint unreachable: {exc!r}") from exc
        if response.status_code != 200:
            raise ServiceTokenError(
                f"Token endpoint returned {response.status_code}", status_code=response.status_code
            )

        body = response.json()
        token = body["access_token"]
        try:
            exp = jwt.decode(token, options={"verify_signature": False}).get("exp")
        except jwt.PyJWTError:
            exp = None
        if exp is None:
            exp = now + float(body.get("expires_in", 0))
        self._token, self._expires_at = token, float(exp)
        return token


class ServiceAuth(httpx.Auth):
    """Bearer token from the provider; on 401 refetches the token and retries once."""

    def __init__(self, provider: ServiceTokenProvider):
        self._provider = provider

    def sync_auth_flow(self, request):
        raise RuntimeError("ServiceAuth works with httpx.AsyncClient only")

    async def async_auth_flow(self, request):
        token = await self._provider.get_token()
        request.headers["Authorization"] = f"Bearer {token}"
        response = yield request
        if response.status_code == 401:
            self._provider.invalidate(token)
            request.headers["Authorization"] = f"Bearer {await self._provider.get_token()}"
            yield request


def client(
    base_url: str,
    token_provider: ServiceTokenProvider,
    *,
    on_behalf_of: str | None = None,
    request_id: str | None = None,
    **kwargs,
) -> httpx.AsyncClient:
    """`httpx.AsyncClient` whose requests share one X-Request-ID (`request_id`, or a new one)
    and carry X-On-Behalf-Of when given. Extra kwargs go to httpx."""
    headers = {REQUEST_ID_HEADER: request_id or uuid.uuid4().hex}
    if on_behalf_of:
        headers[ON_BEHALF_OF_HEADER] = on_behalf_of
    headers.update(kwargs.pop("headers", None) or {})
    kwargs.setdefault("timeout", 10.0)
    return httpx.AsyncClient(base_url=base_url, headers=headers, auth=ServiceAuth(token_provider), **kwargs)
