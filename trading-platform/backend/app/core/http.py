"""Request plumbing: request ids, client address, the Origin check and security headers."""

import ipaddress
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from .config import get_settings
from .logging import request_id_var

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Machine endpoints authenticate with their own token and never use the
# browser session cookie, so the browser-oriented Origin check does not apply.
MACHINE_PATH_PREFIXES = ("/api/v1/hooks/", "/api/v1/bridge/")


@dataclass(frozen=True)
class RequestMeta:
    ip: str | None
    user_agent: str | None
    request_id: str


def client_ip(request: Request) -> str | None:
    """The caller's address, trusting only the configured number of proxies.

    X-Forwarded-For is client-controlled except for the entries appended by
    our own proxies, so we count back from the right and ignore the rest.
    """
    hops = get_settings().trusted_proxy_hops
    peer = request.client.host if request.client else None
    if hops <= 0:
        return peer
    forwarded = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    if len(forwarded) < hops:
        return peer
    candidate = forwarded[-hops]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return peer


def request_meta(request: Request) -> RequestMeta:
    agent = request.headers.get("user-agent")
    return RequestMeta(
        ip=client_ip(request),
        user_agent=agent[:300] if agent else None,
        request_id=getattr(request.state, "request_id", ""),
    )


def _origin_of(url: str) -> str | None:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request id, blocks cross-site writes and sets security headers."""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = uuid.uuid4().hex
        request.state.request_id = request_id
        token = request_id_var.set(request_id)
        try:
            blocked = self._cross_site_write(request)
            response = blocked if blocked is not None else await call_next(request)
        finally:
            request_id_var.reset(token)

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers.setdefault("Cache-Control", "no-store")
        if get_settings().cookie_secure:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response

    @staticmethod
    def _cross_site_write(request: Request) -> Response | None:
        """Refuse state-changing browser requests that come from another site.

        Browsers attach an Origin header to cross-site writes, so a forged
        request from a malicious page is recognisable. Non-browser clients send
        neither header and are let through; they cannot carry a victim's cookie.
        """
        if request.method not in UNSAFE_METHODS or request.url.path.startswith(MACHINE_PATH_PREFIXES):
            return None
        origin = request.headers.get("origin")
        if origin is None:
            referer = request.headers.get("referer")
            origin = _origin_of(referer) if referer else None
            if origin is None:
                return None
        if origin.rstrip("/") in get_settings().allowed_origins:
            return None
        return JSONResponse(
            {"error": {"code": "origin_not_allowed", "message": "This request came from an unexpected site."}},
            status_code=403,
        )
