import json
import logging

import redis.asyncio as redis

from .config import SSOConfig

logger = logging.getLogger(__name__)

_KEY_PREFIX = "authentik-sso:session:"


class SessionStore:
    """Server-side хранилище payload'а сессии (user/id_token/refresh_token/expires_at).

    Cookie хранит только opaque sid, указывающий сюда — см. auth.py._store_token/
    _get_valid_user. Отдельный db-индекс от arq (см. config.py) исключает коллизии
    ключей на том же физическом Redis.
    """

    def __init__(self, config: SSOConfig):
        self._redis = redis.Redis(
            host=config.session_redis_host,
            port=config.session_redis_port,
            db=config.session_redis_db,
            decode_responses=True,
        )
        self._ttl = config.session_ttl_seconds

    def _key(self, sid: str) -> str:
        return f"{_KEY_PREFIX}{sid}"

    async def get(self, sid: str) -> dict | None:
        # GETEX — чтение и продление TTL (sliding-сессия) одним round-trip'ом
        raw = await self._redis.getex(self._key(sid), ex=self._ttl)
        return json.loads(raw) if raw is not None else None

    async def set(self, sid: str, data: dict) -> None:
        await self._redis.set(self._key(sid), json.dumps(data), ex=self._ttl)

    async def pop(self, sid: str) -> dict | None:
        # GETDEL — атомарные fetch+delete, для /logout
        raw = await self._redis.getdel(self._key(sid))
        return json.loads(raw) if raw is not None else None

    async def delete(self, sid: str) -> None:
        await self._redis.delete(self._key(sid))
