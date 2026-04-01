import { serialize } from "cookie";

export async function POST() {
  const cookie = serialize("token", "", {
    httpOnly: true,
    expires: new Date(0),
    path: "/",
  });

  return new Response(JSON.stringify({ message: "Logged out" }), {
    headers: {
      "Set-Cookie": cookie,
    },
  });
}