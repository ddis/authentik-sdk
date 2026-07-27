import asyncio
import logging
import time
from urllib.parse import urlencode

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse

from .config import SSOConfig

logger = logging.getLogger(__name__)

# сколько секунд запаса брать до истечения access/id-токена, чтобы не словить
# протухший токен из-за задержки между проверкой и фактическим запросом
EXPIRY_LEEWAY_SECONDS = 30


class AuthentikAuth:
    """OIDC-клиент Authentik: роутер (/login, /auth/callback, /logout, /api/me)
    + FastAPI-dependency (require_user/get_current_user), которые шарят один
    и тот же OAuth-клиент — это нужно, чтобы dependency могла молча обновлять
    токен по refresh_token, когда он истёк.

    Usage:
        auth = AuthentikAuth(config)
        app.include_router(auth.router)

        @app.get("/api/whoami")
        async def whoami(user: dict = Depends(auth.require_user)):
            ...
    """

    def __init__(self, config: SSOConfig):
        self.config = config
        self.oauth = OAuth()
        self.oauth.register(
            name="authentik",
            server_metadata_url=f"{config.issuer}.well-known/openid-configuration",
            client_id=config.client_id,
            client_secret=config.client_secret,
            client_kwargs={"scope": config.scope},
        )
        self.router = self._build_router()
        # single-flight для обновления токена: сессия — это подписанная cookie
        # без общего server-side стора, поэтому несколько параллельных запросов
        # с одним и тем же (ещё не обновлённым в их cookie) refresh_token могут
        # одновременно попытаться его обменять. Держим один и тот же asyncio.Task
        # на refresh_token, чтобы Authentik дёргался один раз, а все параллельные
        # запросы получили один и тот же результат и записали его каждый в свою
        # cookie — тогда неважно, чей Set-Cookie в итоге "победит" в браузере.
        self._refresh_tasks: dict[str, asyncio.Task] = {}
        self._refresh_tasks_guard = asyncio.Lock()

    @property
    def client(self):
        return self.oauth.authentik

    def _build_router(self) -> APIRouter:
        config = self.config
        client = self.client

        router = APIRouter()

        @router.get("/login")
        async def login(request: Request, next: str = "/"):
            # только относительные пути своего фронтенда — иначе открытый редирект
            safe_next = next if next.startswith("/") and not next.startswith("//") else "/"
            request.session["next"] = safe_next
            redirect_uri = request.url_for("auth_callback")
            return await client.authorize_redirect(request, redirect_uri)

        @router.get("/auth/callback")
        async def auth_callback(request: Request):
            token = await client.authorize_access_token(request)
            self._store_token(request, token)
            next_path = request.session.pop("next", "/")
            return RedirectResponse(f"{config.frontend_url}{next_path}")

        @router.get("/logout")
        async def logout(request: Request):
            id_token = request.session.get("id_token")
            request.session.clear()

            metadata = await client.load_server_metadata()
            end_session_endpoint = metadata.get("end_session_endpoint")
            if not end_session_endpoint:
                return RedirectResponse(config.frontend_url)

            params = {"post_logout_redirect_uri": config.frontend_url}
            if id_token:
                params["id_token_hint"] = id_token
            return RedirectResponse(f"{end_session_endpoint}?{urlencode(params)}")

        @router.get("/api/me")
        async def me(request: Request):
            user = await self._get_valid_user(request)
            if not user:
                return JSONResponse({"authenticated": False}, status_code=401)
            return {"authenticated": True, "user": user}

        return router

    def _store_token(self, request: Request, token: dict) -> None:
        request.session["user"] = token["userinfo"]
        request.session["id_token"] = token.get("id_token")
        request.session["refresh_token"] = token.get("refresh_token")
        request.session["expires_at"] = token.get("expires_at")

    async def _get_valid_user(self, request: Request) -> dict | None:
        user = request.session.get("user")
        if not user:
            logger.debug("No user in session (not logged in)")
            return None

        expires_at = request.session.get("expires_at")
        if expires_at is not None and time.time() < expires_at - EXPIRY_LEEWAY_SECONDS:
            return user

        refresh_token = request.session.get("refresh_token")
        if not refresh_token:
            # нечем освежить (провайдер не выдал refresh_token, либо мы его ещё
            # не сохраняли) — считаем сессию истёкшей
            logger.warning(
                "No refresh_token in session for user %r (expires_at=%r) — treating session as expired",
                user.get("email") or user.get("preferred_username"),
                expires_at,
            )
            request.session.clear()
            return None

        try:
            token = await self._refresh_access_token(refresh_token)
        except Exception:
            # Authentik отклонил refresh — например, база пересоздана заново
            # и такого refresh_token/сессии там больше не существует
            logger.warning("Token refresh failed for session", exc_info=True)
            request.session.clear()
            return None

        # Диагностика подозрения "Authentik не отдаёт refresh_token на обновлении":
        # .get(key, default) подставит default, ТОЛЬКО если ключа нет вообще;
        # если Authentik явно прислал "refresh_token": null, вернётся None,
        # а не старый refresh_token — сессия молча лишится возможности
        # обновиться в следующий раз. Логируем оба случая, чтобы это увидеть.
        if "refresh_token" not in token:
            logger.info("Refresh response has no refresh_token key — reusing previous one")
        elif token.get("refresh_token") is None:
            logger.warning(
                "Refresh response has refresh_token=None explicitly — session will lose it "
                "instead of reusing the previous (still valid) one"
            )

        request.session["id_token"] = token.get("id_token", request.session.get("id_token"))
        request.session["refresh_token"] = token.get("refresh_token", refresh_token)
        request.session["expires_at"] = token.get("expires_at")
        return request.session["user"]

    async def _refresh_access_token(self, refresh_token: str) -> dict:
        """Обменивает refresh_token на новый access-токен, схлопывая параллельные
        вызовы с одним и тем же refresh_token в один запрос к Authentik."""
        async with self._refresh_tasks_guard:
            task = self._refresh_tasks.get(refresh_token)
            if task is None:
                task = asyncio.ensure_future(
                    self.client.fetch_access_token(
                        refresh_token=refresh_token,
                        grant_type="refresh_token",
                    )
                )
                self._refresh_tasks[refresh_token] = task

        try:
            return await asyncio.shield(task)
        finally:
            async with self._refresh_tasks_guard:
                if self._refresh_tasks.get(refresh_token) is task:
                    del self._refresh_tasks[refresh_token]

    async def get_current_user(self, request: Request) -> dict | None:
        """Возвращает пользователя из сессии, освежая токен при необходимости, или None."""
        return await self._get_valid_user(request)

    async def require_user(self, request: Request) -> dict:
        """FastAPI dependency: 401, если сессии нет или её не удалось освежить.

        Usage: `async def endpoint(user: dict = Depends(auth.require_user))`.
        """
        user = await self._get_valid_user(request)
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
        return user
