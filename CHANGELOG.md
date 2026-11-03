# Changelog

## 0.6.0

One general way for our services to call each other, in place of the old per-service token checks.

### Added
- Service-to-service (m2m) auth: a service calls another with its own Authentik token, optionally on behalf of a user
  - callee: `require_service(Service.ACCOUNTING)` lets in only the listed services
  - caller: `auth.service_client("registry")`, an HTTP client that gets and renews the token itself
  - setup per service: README «M2M»
- `Service`: the list of our services, so a misspelled name fails at startup
- `ENVIRONMENT` (`local` / `dev` / `prod`): which environment the service runs in, used to find other services

### Removed
- `require_staff_token` / `require_headquarters_token` / `require_proposal_token` / `require_avtologistic_token` (unused); use `require_service(Service.…)`
- `SSOConfig.jwks_url` and `AUTHENTIK_JWKS_URL`: JWKS URL is built from the token's issuer

### Changed
- Python ≥ 3.11 (`StrEnum`)

### Upgrading
- remove `AUTHENTIK_JWKS_URL` from `.env`; drop `jwks_url=` where `SSOConfig` is built by hand
- set `ENVIRONMENT` (`local` / `dev` / `prod`) where it isn't `local`
- callers: set `SERVICE_CLIENT_ID` / `SERVICE_CLIENT_SECRET` from their `<svc>-m2m` provider
