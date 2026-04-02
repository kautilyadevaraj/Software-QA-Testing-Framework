import { NextResponse } from "next/server";
import jwt from "jsonwebtoken";

export function proxy(req) {
  const { pathname } = req.nextUrl;
  const token = req.cookies.get("token")?.value;

  const isAuthPage = pathname === "/login" || pathname === "/signup";
  const isUploadPage = pathname.startsWith("/upload");
  const isProjectsPage = pathname.startsWith("/projects");

  if (!token && (isUploadPage || isProjectsPage)) {
    return NextResponse.redirect(new URL("/login", req.url));
  }

  if (token && isAuthPage) {
    try {
      jwt.verify(token, process.env.JWT_SECRET);
      return NextResponse.redirect(new URL("/projects", req.url));
    } catch {
      return NextResponse.next();
    }
  }

  if (token && (isUploadPage || isProjectsPage)) {
    try {
      jwt.verify(token, process.env.JWT_SECRET);
      return NextResponse.next();
    } catch {
      return NextResponse.redirect(new URL("/login", req.url));
    }
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/upload/:path*", "/projects/:path*", "/login", "/signup"],
};