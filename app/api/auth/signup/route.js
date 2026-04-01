import { prisma } from "@/lib/prisma";
import { hash } from "bcrypt";
import { generateToken } from "@/lib/jwt";
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

    // Logic: 8+ chars, 1 Upper, 1 Lower, 1 Number, 1 Special
    const passwordRegex = /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[@$!%*?&])[A-Za-z\d@$!%*?&]{8,}$/;
    if (!passwordRegex.test(password)) {
      return Response.json({ 
        error: "Password needs 8+ chars, uppercase, number, and symbol" 
      }, { status: 400 });
    }

    const existingUser = await prisma.user.findUnique({ where: { email } });
    if (existingUser) {
      return Response.json({ error: "This email already exists", code: "USER_EXISTS" }, { status: 400 });
    }

    const hashedPassword = await hash(password, 10);
    const user = await prisma.user.create({
      data: { email, password: hashedPassword },
    });

    const token = generateToken(user);
    const cookie = serialize("token", token, {
      httpOnly: true,
      secure: process.env.NODE_ENV === "production",
      maxAge: 60 * 60 * 24 * 3,
      path: "/",
    });

    return new Response(JSON.stringify({ user }), {
      status: 201,
      headers: { "Set-Cookie": cookie, "Content-Type": "application/json" },
    });
  } catch (e) {
    return Response.json({ error: "Signup failed" }, { status: 500 });
  }
}