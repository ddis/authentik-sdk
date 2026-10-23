"""Service-to-service (m2m) token verification on the callee side.

Every calling service has its own Authentik provider `<name>-m2m` (client_credentials only):

    iss = <base>/o/accounting-m2m/  -> caller "accounting"
    iss = <base>/o/<app-slug>/      -> user login token, never accepted as m2m (401)
"""

import asyncio
import logging
from typing import TypedDict

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

M2M_SUFFIX = "-m2m"
ON_BEHALF_OF_HEADER = "X-On-Behalf-Of"

# safe margin for clock skew between services and Authentik
EXPIRY_LEEWAY_M2M_SECONDS = 10


class ServiceCaller(TypedDict):
    caller: str  # "accounting"
    on_behalf_of: str | None  # user's OIDC `sub` from X-On-Behalf-Of


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


class M2MVerifier:
    """Verifies RS256 tokens issued by `<base>/o/<slug>/`; one cached JWKS client per slug.

    Only signed (JWS) tokens are supported. Callee's Authentik setup per `<name>-m2m` provider:
    Signing Key = "authentik Self-signed Certificate", Encryption Key unset.
    """

    def __init__(self, issuer: str, jwks_url: str):
        self._issuer_base = issuer.split("/o/")[0]
        self._jwks_base = jwks_url.split("/o/")[0]
        self._jwks_clients: dict[str, jwt.PyJWKClient] = {}

    def issuer(self, slug: str) -> str:
        return f"{self._issuer_base}/o/{slug}/"

    def jwks_url(self, slug: str) -> str:
        return f"{self._jwks_base}/o/{slug}/jwks/"

    def is_m2m_issuer(self, iss) -> bool:
        prefix = f"{self._issuer_base}/o/"
        return (
            isinstance(iss, str)
            and iss.startswith(prefix)
            and iss.endswith(f"{M2M_SUFFIX}/")
            and "/" not in iss[len(prefix) : -1]
        )

    def _jwks_client(self, slug: str) -> jwt.PyJWKClient:
        # PyJWKClient caches the key set itself and refetches on an unknown `kid` (key rotation)
        client = self._jwks_clients.get(slug)
        if client is None:
            client = self._jwks_clients[slug] = jwt.PyJWKClient(self.jwks_url(slug))
        return client

    async def _signing_key(self, slug: str, token: str):
        # JWKS fetch is blocking urllib: keep it off the event loop
        return await asyncio.to_thread(self._jwks_client(slug).get_signing_key_from_jwt, token)

    async def verify(self, slug: str, token: str, audience: str | None = None) -> dict:
        """Claims of a valid token from provider `slug`; 401 if invalid, 503 if JWKS is unreachable."""
        if (parts := len(token.split("."))) != 3:
            logger.warning("M2M token rejected for %s: %d segments", slug, parts)
            raise _unauthorized("Invalid token")

        try:
            signing_key = await self._signing_key(slug, token)
        except jwt.PyJWKClientConnectionError as exc:
            # not the caller's fault: a 4xx here would make it give up for good
            logger.warning("M2M JWKS unreachable for %s: %s", slug, exc)
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Token keys unavailable")
        except jwt.PyJWTError as exc:
            logger.warning("M2M token rejected for %s: %s", slug, exc)
            raise _unauthorized("Invalid token")

        try:
            return jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                issuer=self.issuer(slug),
                audience=audience,
                options={
                    "verify_aud": audience is not None,
                    "require": ["exp", "iss"],
                },
                leeway=EXPIRY_LEEWAY_M2M_SECONDS,
            )
        except jwt.PyJWTError as exc:
            logger.warning("M2M token rejected for %s: %s", slug, exc)
            raise _unauthorized(f"Invalid token: {exc}")

    def require_service(self, *names: str, audience: str | None = None):
        """FastAPI dependency accepting only m2m tokens of the listed services.

            no / bad / user token -> 401
            valid m2m, not listed -> 403
            listed                -> {"caller": "accounting", "on_behalf_of": <X-On-Behalf-Of>}
        """
        if not names:
            raise ValueError("require_service() needs at least one service name")
        allowed = {self.issuer(f"{name}{M2M_SUFFIX}"): name for name in names}
        bearer_scheme = HTTPBearer(auto_error=False)

        async def dependency(
            request: Request,
            bearer: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
        ) -> ServiceCaller:
            if bearer is None:
                raise _unauthorized("Not authenticated")
            token = bearer.credentials
            try:
                iss = jwt.decode(token, options={"verify_signature": False}).get("iss")
            except jwt.PyJWTError as exc:
                logger.warning("M2M token rejected: %s", exc)
                raise _unauthorized("Invalid token")

            caller = allowed.get(iss)
            if caller is None:
                if self.is_m2m_issuer(iss):
                    # decided before signature check: the request is denied either way,
                    # and we never fetch JWKS for a provider slug picked by the caller
                    logger.warning("M2M caller not allowed: iss=%s allowed=%s", iss, list(allowed.values()))
                    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Service not allowed")
                logger.warning("M2M token rejected: not an m2m issuer (iss=%s)", iss)
                raise _unauthorized("Invalid token")

            await self.verify(f"{caller}{M2M_SUFFIX}", token, audience=audience)
            return {
                "caller": caller,
                "on_behalf_of": request.headers.get(ON_BEHALF_OF_HEADER) or None,
            }

        return dependency
