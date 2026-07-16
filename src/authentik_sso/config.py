import os
from dataclasses import dataclass


@dataclass
class SSOConfig:
    issuer: str
    client_id: str
    client_secret: str
    session_secret: str
    frontend_url: str
    scope: str = "openid profile email"
    session_same_site: str = "lax"

    @classmethod
    def from_env(cls) -> "SSOConfig":
        issuer = os.environ["AUTHENTIK_ISSUER"]
        if not issuer.endswith("/"):
            issuer += "/"
        return cls(
            issuer=issuer,
            client_id=os.environ["AUTHENTIK_CLIENT_ID"],
            client_secret=os.environ["AUTHENTIK_CLIENT_SECRET"],
            session_secret=os.environ["SESSION_SECRET"],
            frontend_url=os.environ.get("FRONTEND_URL", "http://localhost:5173"),
            scope=os.environ.get("AUTHENTIK_SCOPE", "openid profile email"),
        )
