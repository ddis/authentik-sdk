# authentik-sso

Переиспользуемый кусок кода для FastAPI-сервисов, которые логинятся через
Authentik: `/login`, `/auth/callback`, `/logout`, `/api/me` + dependency для
защиты эндпоинтов. Каждый сервис — свой Confidential OAuth2 client в
Authentik и своя локальная сессия (cookie); SSO-сессия Authentik избавляет
пользователя от повторного ввода пароля в каждом новом сервисе, но code
exchange и сессия у каждого сервиса свои.

## Установка

Пока без публикации в PyPI — ставится по локальному пути:

```bash
pip install -e /path/to/authentik-sso
```

или в Dockerfile:

```dockerfile
COPY authentik-sso /authentik-sso
RUN pip install /authentik-sso
```

## Использование

```python
from fastapi import Depends, FastAPI

from authentik_sso import (
    SSOConfig,
    add_cors_middleware,
    add_session_middleware,
    create_auth_router,
    require_user,
)

config = SSOConfig.from_env()

app = FastAPI()
add_session_middleware(app, config)
add_cors_middleware(app, config)
app.include_router(create_auth_router(config))


@app.get("/api/secret")
async def secret(user: dict = Depends(require_user)):
    return {"hello": user.get("preferred_username")}
```

## Переменные окружения (`SSOConfig.from_env()`)

| Переменная               | Обязательна | Описание                                                        |
|---------------------------|:-----------:|------------------------------------------------------------------|
| `AUTHENTIK_ISSUER`         | да          | `http://authentik.local/application/o/<slug>/` (со слэшем в конце) |
| `AUTHENTIK_CLIENT_ID`      | да          | Со страницы Provider в Authentik                                  |
| `AUTHENTIK_CLIENT_SECRET`  | да          | Со страницы Provider в Authentik                                  |
| `SESSION_SECRET`           | да          | Случайная строка для подписи cookie сессии                       |
| `FRONTEND_URL`             | нет (default `http://localhost:5173`) | Куда редиректить после логина/логаута, и origin для CORS |
| `AUTHENTIK_SCOPE`          | нет (default `openid profile email`) | Через пробел, добавляй свои кастомные scope-mapping'и |

## Что нужно настроить в Authentik на каждый новый сервис

1. **Providers → Create** → OAuth2/OpenID Provider, Client type **Confidential**,
   Redirect URI: `http://<host сервиса>/auth/callback`.
2. **Applications → Create**, привязать к Provider.
3. Взять `Client ID`/`Client Secret` и **OpenID Configuration Issuer** со
   страницы Provider — оттуда, не угадывать по slug.

## Что пакет не делает (осознанно)

- Не хранит сессию где-то централизованно (Redis и т.п.) — сессия каждого
  сервиса живёт в его собственной cookie. Если понадобится единая сессия на
  все сервисы — это отдельный шаг (shared session store + общий cookie domain),
  сюда пока не добавлено.
- Не проверяет группы/роли из claims — это дело каждого сервиса: бери
  `user["groups"]` (или как называется твой custom claim) из результата
  `require_user`/`get_current_user` и решай сам, пускать ли дальше.
