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
    environment: str = "local"  # local / dev / prod
    # m2m caller side; only services that call other services set these (see README "M2M")
    service_client_id: str | None = None
    service_client_secret: str | None = None

    @classmethod
    def from_env(cls) -> "SSOConfig":
        return cls(
            issuer=os.environ["AUTHENTIK_ISSUER"].rstrip("/") + "/",
            client_id=os.environ["AUTHENTIK_CLIENT_ID"],
            client_secret=os.environ["AUTHENTIK_CLIENT_SECRET"],
            session_secret=os.environ["SESSION_SECRET"],
            frontend_url=os.environ.get("FRONTEND_URL", "http://localhost:5173"),
            scope=os.environ.get("AUTHENTIK_SCOPE", "openid profile email"),
            session_redis_host=os.environ.get("SESSION_REDIS_HOST", "redis"),
            session_redis_port=int(os.environ.get("SESSION_REDIS_PORT", 6379)),
            session_redis_db=int(os.environ.get("SESSION_REDIS_DB", 1)),
            session_ttl_seconds=int(os.environ.get("SESSION_TTL_SECONDS", 14 * 24 * 3600)),
            environment=os.environ.get("ENVIRONMENT") or "local",
            service_client_id=os.environ.get("SERVICE_CLIENT_ID") or None,
            service_client_secret=os.environ.get("SERVICE_CLIENT_SECRET") or None,
        )
