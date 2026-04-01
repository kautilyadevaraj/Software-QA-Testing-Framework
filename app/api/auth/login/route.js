import { prisma } from "@/lib/prisma";
import bcrypt from "bcrypt";
import { generateToken } from "@/lib/jwt";
import { serialize } from "cookie";

export async function POST(req) {
  try {
    const { email, password } = await req.json();

    const user = await prisma.user.findUnique({
      where: { email },
    });

    // Check if user exists
    if (!user) {
      return new Response(JSON.stringify({ error: "User not found", code: "USER_MISSING" }), { 
        status: 404,
        headers: { "Content-Type": "application/json" }
      });
    }

    // Check password
    const valid = await bcrypt.compare(password, user.password);
    if (!valid) {
      return new Response(JSON.stringify({ error: "Incorrect password", code: "WRONG_PASSWORD" }), { 
        status: 401,
        headers: { "Content-Type": "application/json" }
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
  } catch (error) {
    return new Response(JSON.stringify({ error: "Server error" }), { status: 500 });
  }
}