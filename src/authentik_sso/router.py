from urllib.parse import urlencode

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from .config import SSOConfig


def create_auth_router(config: SSOConfig) -> APIRouter:
    """Router with /login, /auth/callback, /logout, /api/me.

    Mount once per service (`app.include_router(create_auth_router(config))`).
    Each service is its own OAuth2 client in Authentik; Authentik's own SSO
    session means the user won't see a login form again in other services,
    but each service still runs its own code exchange and keeps its own
    session cookie.
    """
    oauth = OAuth()
    oauth.register(
        name="authentik",
        server_metadata_url=f"{config.issuer}.well-known/openid-configuration",
        client_id=config.client_id,
        client_secret=config.client_secret,
        client_kwargs={"scope": config.scope},
    )

    router = APIRouter()

    @router.get("/login")
    async def login(request: Request, next: str = "/"):
        # только относительные пути своего фронтенда — иначе открытый редирект
        safe_next = next if next.startswith("/") and not next.startswith("//") else "/"
        request.session["next"] = safe_next
        redirect_uri = request.url_for("auth_callback")
        return await oauth.authentik.authorize_redirect(request, redirect_uri)

    @router.get("/auth/callback")
    async def auth_callback(request: Request):
        token = await oauth.authentik.authorize_access_token(request)
        request.session["user"] = token["userinfo"]
        request.session["id_token"] = token.get("id_token")
        next_path = request.session.pop("next", "/")
        return RedirectResponse(f"{config.frontend_url}{next_path}")

    @router.get("/logout")
    async def logout(request: Request):
        id_token = request.session.get("id_token")
        request.session.clear()

        metadata = await oauth.authentik.load_server_metadata()
        end_session_endpoint = metadata.get("end_session_endpoint")
        if not end_session_endpoint:
            return RedirectResponse(config.frontend_url)

        params = {"post_logout_redirect_uri": config.frontend_url}
        if id_token:
            params["id_token_hint"] = id_token
        return RedirectResponse(f"{end_session_endpoint}?{urlencode(params)}")

    @router.get("/api/me")
    async def me(request: Request):
        user = request.session.get("user")
        if not user:
            return JSONResponse({"authenticated": False}, status_code=401)
        return {"authenticated": True, "user": user}

    return router
