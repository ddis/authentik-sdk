import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

AUTHENTIK = "http://authentik.local/application"


def make_config(**overrides):
    from authentik_sso import SSOConfig

    return SSOConfig(
        **{
            "issuer": f"{AUTHENTIK}/o/registry/",
            "client_id": "registry",
            "client_secret": "secret",
            "session_secret": "session",
            "frontend_url": "http://localhost:5173",
            **overrides,
        }
    )


@pytest.fixture(scope="session")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def make_token(signing_key):
    """make_token("accounting-m2m") -> RS256 token issued by <AUTHENTIK>/o/accounting-m2m/."""

    def make(slug: str, *, key=None, ttl: int = 300, **claims) -> str:
        payload = {"iss": f"{AUTHENTIK}/o/{slug}/", "sub": "svc", "exp": int(time.time()) + ttl, **claims}
        return jwt.encode(payload, key or signing_key, algorithm="RS256", headers={"kid": "test"})

    return make


class FakeJWKSClient:
    """Stands in for jwt.PyJWKClient: always returns the test public key."""

    instances: list["FakeJWKSClient"] = []

    def __init__(self, uri: str, public_key=None):
        self.uri = uri
        self.public_key = public_key
        self.error: Exception | None = None
        FakeJWKSClient.instances.append(self)

    def get_signing_key_from_jwt(self, token):
        if self.error:
            raise self.error
        return SimpleNamespace(key=self.public_key)


@pytest.fixture
def fake_jwks(monkeypatch, signing_key):
    """Patches PyJWKClient in m2m; returns the list of created clients."""
    from authentik_sso import m2m

    FakeJWKSClient.instances = []
    monkeypatch.setattr(
        m2m.jwt, "PyJWKClient", lambda uri: FakeJWKSClient(uri, signing_key.public_key())
    )
    return FakeJWKSClient.instances


@pytest.fixture
def anyio_backend():
    return "asyncio"
