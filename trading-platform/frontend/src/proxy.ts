import { type NextRequest, NextResponse } from "next/server";

// Where the API lives. Read at runtime, so one build runs in any environment.
const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

const SESSION_COOKIES = ["tp_session", "__Host-tp_session"];
const PUBLIC_PAGES = new Set(["/login", "/register", "/verify-email", "/forgot-password", "/reset-password"]);

export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;

  // The browser talks to one origin; API calls are forwarded to the backend.
  // Same-origin requests keep the session cookie first-party and need no CORS.
  if (pathname.startsWith("/api/")) {
    return NextResponse.rewrite(new URL(pathname + search, BACKEND_URL));
  }

  // Convenience only: send visitors without a session cookie to the sign-in
  // page. Every real access decision is made by the API.
  const hasSession = SESSION_COOKIES.some((name) => request.cookies.has(name));
  if (!hasSession && !PUBLIC_PAGES.has(pathname)) {
    const login = new URL("/login", request.url);
    if (pathname !== "/") login.searchParams.set("next", pathname);
    return NextResponse.redirect(login);
  }
  return NextResponse.next();
}

export const config = {
  // Everything except Next's own assets and static files.
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|ico|txt)$).*)"],
};
