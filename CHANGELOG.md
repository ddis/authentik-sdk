# Changelog

## 0.6.0

Service-to-service (m2m) calls: one Authentik provider `<svc>-m2m` per calling service; see README «M2M».

### Added
- `auth.require_service(Service.ACCOUNTING, …)`: callee dependency accepting only m2m tokens of the listed services
  - no / invalid / user token → 401, service not listed → 403, JWKS unreachable → 503
  - returns `ServiceCaller`: `{"caller": Service.ACCOUNTING, "on_behalf_of": <X-On-Behalf-Of> | None}`
- `Service` enum: our services (accounting, registry, staff, headquarters, proposal, avto-logistic); an unknown name fails at import
- `auth.service_client("registry", on_behalf_of=…, request_id=…)`: `httpx.AsyncClient` calling another service
  - token via `client_credentials`, cached until `exp - 30s`; on 401 refetched and retried once
  - headers `X-Request-ID` (shared by the client's requests) and `X-On-Behalf-Of`
  - base URL `http://api.<name>.<domain>`, domain by `ENVIRONMENT`
- `SSOConfig.environment` (`Environment`: `local` default / `dev` / `prod`) from `ENVIRONMENT`; any other value fails `SSOConfig.from_env()`
- `SSOConfig.service_client_id` / `service_client_secret` from `SERVICE_CLIENT_ID` / `SERVICE_CLIENT_SECRET`; needed only by callers, checked on the first call
- dependency `PyJWT[crypto]`

### Removed
- `require_staff_token` / `require_headquarters_token` / `require_proposal_token` / `require_avtologistic_token` (unused); use `require_service(Service.…)`
- `SSOConfig.jwks_url` and `AUTHENTIK_JWKS_URL`: JWKS URL is built from the token's issuer

### Changed
- Python ≥ 3.11 (`StrEnum`)

### Upgrading
- remove `AUTHENTIK_JWKS_URL` from `.env`; drop `jwks_url=` where `SSOConfig` is built by hand
- set `ENVIRONMENT` (`local` / `dev` / `prod`) where it isn't `local`
- callers: set `SERVICE_CLIENT_ID` / `SERVICE_CLIENT_SECRET` from their `<svc>-m2m` provider
