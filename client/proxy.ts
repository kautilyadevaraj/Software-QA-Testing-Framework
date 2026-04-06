import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

/**
 * Next.js Proxy — runs on every request matching the config.matcher.
 * Guards protected routes by checking for the access_token cookie.
 *
 * Renamed from middleware.ts → proxy.ts (Next.js 16 convention).
 */
export function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  const token = request.cookies.get("access_token")?.value;

  const isAuthPage = pathname === "/login" || pathname === "/signup";
  const isProtectedPage = pathname.startsWith("/projects");

  // Unauthenticated user tries to access protected page → redirect to login
  if (!token && isProtectedPage) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  // Already-authenticated user tries to access login/signup → redirect to projects
  if (token && isAuthPage) {
    return NextResponse.redirect(new URL("/projects", request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/projects/:path*", "/login", "/signup"],
};
