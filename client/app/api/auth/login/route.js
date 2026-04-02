import { prisma } from "@/lib/prisma";
import { generateToken } from "@/lib/jwt";
import { verifyPassword } from "@/lib/password";
import { serialize } from "cookie";

export async function POST(req) {
  try {
    const { email, password } = await req.json();

    const user = await prisma.user.findUnique({
      where: { email },
    });

    if (!user) {
      return new Response(JSON.stringify({ error: "Invalid credentials", code: "INVALID_CREDENTIALS" }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      });
    }

    const valid = await verifyPassword(password, user.password);
    if (!valid) {
      return new Response(JSON.stringify({ error: "Invalid credentials", code: "INVALID_CREDENTIALS" }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      });
    }

    const token = generateToken(user);
    const cookie = serialize("token", token, {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      maxAge: 60 * 60 * 24 * 3,
      path: "/",
      sameSite: "lax",
    });

    return new Response(JSON.stringify({ user }), {
      status: 200,
      headers: {
        "Set-Cookie": cookie,
        "Content-Type": "application/json",
      },
    });
  } catch {
    return new Response(JSON.stringify({ error: "Server error" }), {
      status: 500,
      headers: { "Content-Type": "application/json" },
    });
  }
}