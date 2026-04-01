# 🚀 Software QA Testing Framework (UI)

This is a **Next.js + Prisma** project.

---

# 📦 Project Setup

## 1️⃣ Install dependencies

```bash
npm install
```

---

## 2️⃣ Setup Environment Variables

Rename the example file:

```bash
cp .env.example .env
```

Now update `.env` with your database credentials.

---

# 🗄️ Database Setup (PostgreSQL)

Run the following commands in PostgreSQL:

```sql
CREATE DATABASE authdb;

CREATE USER authuser WITH PASSWORD 'password';

ALTER DATABASE authdb OWNER TO authuser;

ALTER ROLE authuser CREATEDB;

\c authdb

ALTER SCHEMA public OWNER TO authuser;

GRANT ALL ON SCHEMA public TO authuser;

GRANT ALL PRIVILEGES ON DATABASE authdb TO authuser;
```

---

## 3️⃣ Update `.env`

Example:

```env
DATABASE_URL="postgresql://authuser:password@localhost:5432/authdb"
JWT_SECRET="your_secret_here"
```

---

# ⚙️ Prisma Setup

Run the following commands:

```bash
npx prisma generate
npx prisma migrate dev
npx prisma studio
```

* `generate` → creates Prisma client
* `migrate dev` → creates tables in DB
* `studio` → opens DB UI

---

# ▶️ Run the Project

```bash
npm run dev
```

Open:

👉 http://localhost:3000


