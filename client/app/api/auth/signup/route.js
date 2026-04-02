import { prisma } from "@/lib/prisma";
import { generateToken } from "@/lib/jwt";
import { hashPassword } from "@/lib/password";
import { serialize } from "cookie";

export async function POST(req) {
  try {
    const { email, password } = await req.json();

    if (!email || !password) {
      return Response.json({ error: "Missing fields" }, { status: 400 });
    }

    // Email format validation
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailRegex.test(email)) {
      return Response.json({ error: "Invalid email format" }, { status: 400 });
    }

    // Logic: 8-18 chars, 1 Upper, 1 Lower, 1 Number, 1 Special
    const passwordRegex = /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z0-9]).{8,18}$/;
    if (!passwordRegex.test(password)) {
      return Response.json({ 
        error: "Password must be 8-18 chars with uppercase, lowercase, number, and special character" 
      }, { status: 400 });
    }

    const existingUser = await prisma.user.findUnique({ where: { email } });
    if (existingUser) {
      return Response.json({ error: "This email already exists", code: "USER_EXISTS" }, { status: 400 });
    }

    const hashedPassword = await hashPassword(password);
    const user = await prisma.user.create({
      data: { email, password: hashedPassword },
    });

    const token = generateToken(user);
    const cookie = serialize("token", token, {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      maxAge: 60 * 60 * 24 * 3,
      path: "/",
      sameSite: "lax",
    });

    return new Response(JSON.stringify({ user }), {
      status: 201,
      headers: { "Set-Cookie": cookie, "Content-Type": "application/json" },
    });
  } catch {
    return Response.json({ error: "Signup failed" }, { status: 500 });
  }
}