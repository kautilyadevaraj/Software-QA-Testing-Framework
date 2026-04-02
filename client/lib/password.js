import bcrypt from "bcrypt";

function getSaltRounds() {
  const parsed = Number.parseInt(process.env.BCRYPT_SALT_ROUNDS || "10", 10);
  if (!Number.isInteger(parsed) || parsed < 10 || parsed > 15) {
    return 10;
  }
  return parsed;
}

function withPepper(password) {
  const pepper = process.env.BCRYPT_PEPPER || "";
  return `${password}${pepper}`;
}

export async function hashPassword(password) {
  return bcrypt.hash(withPepper(password), getSaltRounds());
}

export async function verifyPassword(password, passwordHash) {
  return bcrypt.compare(withPepper(password), passwordHash);
}
