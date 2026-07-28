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
    # server-side сессия (Redis) — cookie несёт только opaque sid, см. store.py.
    # Отдельный namespace SESSION_REDIS_*, а не REDIS_HOST/PORT — чтобы не
    # путать с собственным redis сервиса (например, под arq-очередь)
    session_redis_host: str = "redis"
    session_redis_port: int = 6379
    session_redis_db: int = 1
    session_ttl_seconds: int = 14 * 24 * 3600  # как дефолтный max_age SessionMiddleware

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
            session_redis_host=os.environ.get("SESSION_REDIS_HOST", "redis"),
            session_redis_port=int(os.environ.get("SESSION_REDIS_PORT", 6379)),
            session_redis_db=int(os.environ.get("SESSION_REDIS_DB", 1)),
            session_ttl_seconds=int(os.environ.get("SESSION_TTL_SECONDS", 14 * 24 * 3600)),
        )
