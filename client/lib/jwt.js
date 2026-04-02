import jwt from "jsonwebtoken";

export function generateToken(user) {
  if (!process.env.JWT_SECRET) {
    throw new Error("JWT_SECRET is not configured");
  }

  const jwtExpiration = process.env.JWT_EXPIRATION || "3d";

  return jwt.sign(
    { userId: user.id, email: user.email },
    process.env.JWT_SECRET,
    { expiresIn: jwtExpiration }
  );
}

export function verifyToken(token) {
  try {
    if (!process.env.JWT_SECRET) {
      return null;
    }

    return jwt.verify(token, process.env.JWT_SECRET);
  } catch {
    return null;
  }
}