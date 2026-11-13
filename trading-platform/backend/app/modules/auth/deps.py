"""FastAPI dependencies that authenticate a request and set its database access context."""

from dataclasses import dataclass

from fastapi import Depends, Request, Response
from sqlalchemy.orm import Session

from app.core import tokens
from app.core.config import get_settings
from app.core.db import get_db, set_access_context, staff_context, tenant_context
from app.core.errors import AppError
from app.core.http import UNSAFE_METHODS
from app.models.identity import STAFF_ROLES, AuthSession, User, UserRole

from . import service

NOT_AUTHENTICATED = AppError(401, "not_authenticated", "Sign in to continue.")


@dataclass
class AuthContext:
    user: User
    session: AuthSession
    raw_token: str

    @property
    def csrf_token(self) -> str:
        return tokens.csrf_token_for(self.raw_token, get_settings().secret_key)


def _drop_cookie_header() -> dict[str, str]:
    settings = get_settings()
    response = Response()
    response.delete_cookie(settings.cookie_name, path="/", httponly=True, secure=settings.cookie_secure, samesite="lax")
    return {"set-cookie": response.headers["set-cookie"]}


def session_from_request(request: Request, db: Session = Depends(get_db)) -> AuthContext:
    """Any live session, including one still waiting for its second factor.

    From here on the database session acts as this user only. State-changing
    requests must also carry the session's CSRF token.
    """
    raw_token = request.cookies.get(get_settings().cookie_name)
    if not raw_token:
        raise NOT_AUTHENTICATED
    resolved = service.resolve_session(db, raw_token)
    if resolved is None:
        # The cookie is dead (signed out, expired, revoked): tell the browser to drop it.
        raise AppError(401, "not_authenticated", "Sign in to continue.", headers=_drop_cookie_header())
    session, user = resolved
    ctx = AuthContext(user=user, session=session, raw_token=raw_token)
    if request.method in UNSAFE_METHODS:
        supplied = request.headers.get("x-csrf-token", "")
        if not supplied or not tokens.constant_time_equals(supplied, ctx.csrf_token):
            raise AppError(403, "csrf_failed", "Your session could not be confirmed. Reload the page and try again.")
    set_access_context(db, tenant_context(user.id))
    return ctx


def current_user(ctx: AuthContext = Depends(session_from_request)) -> AuthContext:
    """A fully signed-in user."""
    if ctx.session.pending_mfa:
        raise AppError(401, "mfa_required", "Enter your two-factor code to finish signing in.")
    return ctx


def _staff(ctx: AuthContext, db: Session, *, roles: set[str]) -> AuthContext:
    if ctx.user.role not in roles:
        # Same answer as for a route that does not exist: do not advertise admin endpoints.
        raise AppError(404, "not_found", "Not found.")
    if ctx.session.mfa_at is None:
        raise AppError(
            403,
            "staff_mfa_required",
            "Admin access requires two-factor authentication. Turn it on, then sign in again.",
        )
    set_access_context(db, staff_context(ctx.user.id))
    return ctx


def current_staff(ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)) -> AuthContext:
    """Support or super admin, signed in with a second factor. Switches to the staff access context."""
    return _staff(ctx, db, roles=STAFF_ROLES)


def current_super_admin(ctx: AuthContext = Depends(current_user), db: Session = Depends(get_db)) -> AuthContext:
    return _staff(ctx, db, roles={UserRole.SUPER_ADMIN.value})
