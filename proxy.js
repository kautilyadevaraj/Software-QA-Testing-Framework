import { NextResponse } from "next/server";
import jwt from "jsonwebtoken";

export function proxy(req) { // ✅ CHANGE HERE
  const token = req.cookies.get("token")?.value;

  if (!token) {
    return NextResponse.redirect(new URL("/login", req.url));
  }

  try {
    jwt.verify(token, process.env.JWT_SECRET);
    return NextResponse.next();
  } catch {
    return NextResponse.redirect(new URL("/login", req.url));
  }
}

export const config = {
  matcher: ["/upload"], // ✅ PROTECT THIS ROUTE
};