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
| `SESSION_REDIS_HOST`       | нет (default `redis`) | Хост Redis под server-side сессию (см. "Сессия (server-side, Redis)") |
| `SESSION_REDIS_PORT`       | нет (default `6379`) | Порт Redis под сессию |
| `SESSION_REDIS_DB`         | нет (default `1`)    | db-индекс, отдельный от возможной своей очереди сервиса (например arq на `0`) |
| `SESSION_TTL_SECONDS`      | нет (default `1209600`, 14 дней) | Sliding TTL сессии в Redis |

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

## Роли/группы (`require_group`)

```python
@app.get("/api/admin/stats")
async def admin_stats(user: dict = Depends(auth.require_group("proposal-admins"))):
    ...
```

403, если у пользователя нет указанной группы; 401, если не залогинен вообще
(`require_group` сам вызывает `require_user` внутри).

Чтобы `user["groups"]` вообще появился в userinfo, в Authentik нужно:

1. **Customization → Property Mappings → Create** → Scope Mapping,
   Scope name `groups`, Expression:
   ```python
   return {"groups": [group.name for group in request.user.ak_groups.all()]}
   ```
2. В Provider'е сервиса, в разделе **Scopes**, добавить этот маппинг в
   "Выбранные области" (рядом с `offline_access`, см. выше).
3. В `.env` сервиса добавить `groups` в `AUTHENTIK_SCOPE`, например:
   `AUTHENTIK_SCOPE=openid profile email offline_access groups`.
4. Завести в Authentik группу (например `proposal-admins`) и добавить в неё
   нужных пользователей — `require_group("proposal-admins")` проверяет имя
   группы буквально.

Без этих шагов `user.get("groups")` будет `None`/отсутствовать, и
`require_group` всегда будет отдавать 403 — это ожидаемо, а не баг SDK.

## Сессия (server-side, Redis)

С 0.4.0 сессия хранится не в cookie, а в Redis: cookie несёт только opaque
`sid` (`SessionMiddleware`, подписан `SESSION_SECRET`, как и раньше), а
`user`/`id_token`/`refresh_token`/`expires_at` лежат в Redis по ключу
`authentik-sso:session:<sid>`, TTL sliding (продлевается на каждый запрос,
дефолт 14 дней — как раньше был `max_age` cookie). Так решена проблема
переполнения `Set-Cookie` (браузерный лимит ~4KB) для юзеров с большим
числом Authentik-групп.

Свой db-индекс (`SESSION_REDIS_DB`, дефолт `1`), отдельный от того, что
сервис может использовать под свою очередь задач (например arq на db `0`) —
на одном физическом Redis, без коллизий ключей. Env-переменные:
`SESSION_REDIS_HOST` (дефолт `redis`), `SESSION_REDIS_PORT` (дефолт `6379`),
`SESSION_REDIS_DB` (дефолт `1`), `SESSION_TTL_SECONDS` (дефолт `1209600`).

Redis тут — обязательная зависимость рантайма (не опциональная): если он
недоступен, `require_user`/`get_current_user` отдают "не залогинен" (401),
никогда не 500 — но залогиниться/остаться залогиненным без Redis нельзя.
Сессии не переживают потерю данных Redis (рестарт без persistence-volume,
`FLUSHDB` и т.п.) — это осознанный компромисс: единственная альтернатива
была бы или снова раздувать cookie, или тащить persistence как обязательное
требование к инфраструктуре потребителя.

## Что пакет не делает (осознанно)

- Сессия каждого сервиса всё ещё изолирована — своя cookie, свой Redis/db-индекс.
  Если понадобится единая сессия на все сервисы — это отдельный шаг (shared
  session store + общий cookie domain), сюда пока не добавлено.
- Рассчитан на один frontend-origin на сервис (`FRONTEND_URL`) — CORS и
  редирект после `/auth/callback` всегда ведут на этот единственный адрес.
  Если сервису нужно несколько независимых UI на разных hostname с общим
  backend — это не поддерживается напрямую; варианты: (а) один frontend с
  role-gated роутами (`require_group` + фильтрация UI по `user.groups`), либо
  (б) доработка SDK под multi-origin allowlist (сюда пока не добавлено).
