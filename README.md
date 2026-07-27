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

from authentik_sso import SSOConfig, AuthentikAuth, add_cors_middleware, add_session_middleware

config = SSOConfig.from_env()

app = FastAPI()
add_session_middleware(app, config)
add_cors_middleware(app, config)

auth = AuthentikAuth(config)
app.include_router(auth.router)


@app.get("/api/secret")
async def secret(user: dict = Depends(auth.require_user)):
    return {"hello": user.get("preferred_username")}
```

`auth.router` и `auth.require_user`/`auth.get_current_user` шарят один и тот же
OAuth-клиент — это важно, потому что `require_user` при истёкшем токене сама
делает refresh через тот же клиент (см. ниже), а не только читает cookie.

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
2. В том же Provider, в разделе **Scopes** — добавить в "Выбранные области"
   маппинг **`authentik default OAuth Mapping: OpenID 'offline_access'`**.
   Без этого шага Authentik не будет выдавать `refresh_token` вообще
   (даже если клиент его запрашивает через `AUTHENTIK_SCOPE`) — сессия
   будет молча падать в 401 каждые ~5 минут (или сколько настроен Access
   Token Validity), без возможности тихого refresh. Это шаг легко забыть —
   он не выбран по умолчанию при создании Provider'а.
3. **Applications → Create**, привязать к Provider.
4. Взять `Client ID`/`Client Secret` и **OpenID Configuration Issuer** со
   страницы Provider — оттуда, не угадывать по slug.

## Refresh токена

`require_user`/`get_current_user` перед тем как отдать пользователя проверяют
`expires_at` в сессии. Если токен истёк (с запасом 30 секунд) — делают refresh
через `refresh_token` тем же OAuth-клиентом:

- refresh прошёл — в сессии обновляются `id_token`/`refresh_token`/`expires_at`,
  профиль пользователя (`user`) при этом не перевыпускается заново — он остаётся
  таким же, каким был получен при первом логине (в refresh-ответе Authentik
  необязательно присылает новый `id_token` с claims, поэтому мы не пытаемся
  их перепарсить без nonce).
- refresh не прошёл (`refresh_token` невалиден, отозван, Authentik не знает про
  такую сессию — например, база Authentik была пересоздана) — сессия чистится,
  `require_user` кидает 401, фронт уходит на `/login` заново.
- `refresh_token` в сессии нет вообще (Authentik его не выдал) — то же самое,
  сессия считается истёкшей по `expires_at` без возможности продлить. Обычно
  это значит, что у Provider'а в Authentik не подключён scope `offline_access`
  (см. "Что нужно настроить в Authentik на каждый новый сервис" выше).

Без этого механизма cookie считалась бы валидной всё время жизни самой cookie
(у `SessionMiddleware` это 14 дней по умолчанию), независимо от того, жив ли
токен на стороне Authentik.

## Что пакет не делает (осознанно)

- Не хранит сессию где-то централизованно (Redis и т.п.) — сессия каждого
  сервиса живёт в его собственной cookie. Если понадобится единая сессия на
  все сервисы — это отдельный шаг (shared session store + общий cookie domain),
  сюда пока не добавлено.
- Не проверяет группы/роли из claims — это дело каждого сервиса: бери
  `user["groups"]` (или как называется твой custom claim) из результата
  `require_user`/`get_current_user` и решай сам, пускать ли дальше.
