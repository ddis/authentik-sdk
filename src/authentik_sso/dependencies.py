from fastapi import HTTPException, Request, status


def get_current_user(request: Request) -> dict | None:
    """Returns the session user dict, or None if not authenticated."""
    return request.session.get("user")


def require_user(request: Request) -> dict:
    """FastAPI dependency: 401s if the request has no authenticated session.

    Usage: `async def endpoint(user: dict = Depends(require_user))`.
    """
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user
