from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from .config import SSOConfig


def add_session_middleware(app: FastAPI, config: SSOConfig) -> None:
    app.add_middleware(
        SessionMiddleware,
        secret_key=config.session_secret,
        same_site=config.session_same_site,
    )


def add_cors_middleware(app: FastAPI, config: SSOConfig) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[config.frontend_url],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
