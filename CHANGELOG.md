# Changelog

## 0.6.0

One general way for our services to call each other, in place of the old per-service token checks.

### Added
- Service-to-service (m2m) auth: a service calls another with its own Authentik token, optionally on behalf of a user
  - callee: `require_service(Service.ACCOUNTING)` lets in only the listed services
  - caller: `auth.service_client("registry")`, an HTTP client that gets and renews the token itself
  - tokens and Authentik's signing keys are cached: no Authentik round-trip per request (0.5.0 fetched the keys on every check)
  - setup per service: README «M2M»
- `Service`: the list of our services, so a misspelled name fails at startup
- `ENVIRONMENT` (`local` / `dev` / `prod`): which environment the service runs in, used to find other services

### Removed
- `require_staff_token` / `require_headquarters_token` / `require_proposal_token` / `require_avtologistic_token` (unused); use `require_service(Service.…)`
- `SSOConfig.jwks_url`: the keys URL now comes from the token's issuer

### Changed
- Python ≥ 3.11

### .env
- removed: `AUTHENTIK_JWKS_URL`
- added: `ENVIRONMENT` = `local` (default) / `dev` / `prod`; any other value fails at startup
- added, only in services that call others: `SERVICE_CLIENT_ID` / `SERVICE_CLIENT_SECRET`, from the service's `<svc>-m2m` provider in Authentik
