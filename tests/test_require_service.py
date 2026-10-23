import logging

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from authentik_sso import AuthentikAuth, SSOConfig


@pytest.fixture
def auth():
    return AuthentikAuth(
        SSOConfig(
            issuer="http://authentik.local/application/o/registry/",
            jwks_url="http://authentik-server:9000/application/o/registry/",
            client_id="registry",
            client_secret="secret",
            session_secret="session",
            frontend_url="http://localhost:5173",
        )
    )


@pytest.fixture
def api(auth, fake_jwks):
    app = FastAPI()

    @app.get("/internal")
    async def internal(svc: dict = Depends(auth.require_service("accounting", "staff"))):
        return svc

    @app.get("/internal-aud")
    async def internal_aud(svc: dict = Depends(auth.require_service("accounting", audience="registry"))):
        return svc

    return TestClient(app)


def call(api, token=None, path="/internal", **headers):
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return api.get(path, headers=headers)


def test_allowed_service_gets_caller_and_on_behalf_of(api, make_token):
    r = call(api, make_token("accounting-m2m"), **{"X-On-Behalf-Of": "user-sub-1"})
    assert r.status_code == 200
    assert r.json() == {"caller": "accounting", "on_behalf_of": "user-sub-1"}


def test_on_behalf_of_is_optional(api, make_token):
    r = call(api, make_token("staff-m2m"))
    assert r.json() == {"caller": "staff", "on_behalf_of": None}


def test_unknown_caller_is_403(api, make_token):
    assert call(api, make_token("headquarters-m2m")).status_code == 403


def test_user_login_token_is_401(api, make_token):
    # user tokens come from the app's own provider, not a `-m2m` one
    assert call(api, make_token("registry")).status_code == 401
    assert call(api, make_token("accounting")).status_code == 401


def test_other_authentik_host_is_401(api, signing_key):
    token = jwt.encode(
        {"iss": "http://other.host/application/o/accounting-m2m/", "exp": 2**31},
        signing_key,
        algorithm="RS256",
    )
    assert call(api, token).status_code == 401


@pytest.mark.parametrize("token", [None, "garbage", "a.b.c"])
def test_missing_or_malformed_token_is_401(api, token):
    assert call(api, token).status_code == 401


def test_foreign_signature_is_401(api, make_token):
    foreign = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert call(api, make_token("accounting-m2m", key=foreign)).status_code == 401


def test_expired_token_is_401(api, make_token):
    assert call(api, make_token("accounting-m2m", ttl=-60)).status_code == 401


def test_audience_not_checked_by_default(api, make_token):
    assert call(api, make_token("accounting-m2m", aud="someone-else")).status_code == 200


def test_audience_checked_when_given(api, make_token):
    assert call(api, make_token("accounting-m2m", aud="registry"), path="/internal-aud").status_code == 200
    assert call(api, make_token("accounting-m2m", aud="staff"), path="/internal-aud").status_code == 401
    assert call(api, make_token("accounting-m2m"), path="/internal-aud").status_code == 401


def test_jwks_client_cached_per_process(api, make_token, fake_jwks):
    for _ in range(3):
        assert call(api, make_token("accounting-m2m")).status_code == 200
    assert [c.uri for c in fake_jwks] == ["http://authentik-server:9000/application/o/accounting-m2m/jwks/"]


def test_jwks_unreachable_is_503(api, make_token, fake_jwks):
    call(api, make_token("accounting-m2m"))
    fake_jwks[0].error = jwt.PyJWKClientConnectionError("connection refused")
    assert call(api, make_token("accounting-m2m")).status_code == 503


def test_no_raw_token_in_logs(api, make_token, caplog):
    caplog.set_level(logging.DEBUG)
    tokens = [
        make_token("accounting-m2m"),
        make_token("headquarters-m2m"),
        make_token("registry"),
        make_token("accounting-m2m", ttl=-60),
    ]
    for token in tokens:
        call(api, token)
    assert caplog.records
    for token in tokens:
        assert token not in caplog.text
        assert token.split(".")[2] not in caplog.text


def test_require_service_needs_a_name(auth):
    with pytest.raises(ValueError):
        auth.require_service()


def test_legacy_per_service_dependency_still_works(auth, fake_jwks, make_token):
    app = FastAPI()

    @app.get("/legacy")
    async def legacy(data: dict = Depends(auth.require_staff_token)):
        return {"iss": data["iss"]}

    api = TestClient(app)
    assert call(api, make_token("staff"), path="/legacy").status_code == 200
    assert call(api, make_token("proposal"), path="/legacy").status_code == 401
