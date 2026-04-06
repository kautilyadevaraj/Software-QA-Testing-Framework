# Software QA Testing Framework (UI)

This is a **Next.js + Prisma** project.

# Project Setup

## 1. Install dependencies

```bash
npm ci
```

## 2. Setup PostgreSQL locally

Install PostgreSQL on your machine (version 17 or later is recommended)

### Using CMD (Windows)

1. Open PostgreSQL
```bash
psql -U postgres
```

2. Create a Database
```sql
CREATE DATABASE sqat_db;
```

3. No extra user/role authorization setup is required for local development.

### Using pgAdmin

1. Create a Server Group in pgAdmin called SQAT
2. Register a Server called SQAT-Server inside SQAT server group
   - In connection tab set
   - Hostname/address : localhost
   - Port : 5432
   - Username : postgres
   - Password : <YOUR_PASSWORD>
   - Check the save password and Save
3. Open the SQAT -> SQAT-Server and right click Databases
4. Create a new DB named as sqat_db and set the owner as postgres

Connection key
```bash
postgresql://{username}:{password}@host:port/{database_name}
```




## 2. Setup PostgreSQL locally (Using pgAdmin)

Install PostgreSQL (version 17 or later recommended) and open pgAdmin.

### 1. Register Server (if not already)

1. Open pgAdmin
2. Right-click **Servers** → Click **Register → Server**
3. In **General** tab:
   * Name: `SQAT-Server`
4. In **Connection** tab:
   * Host: `localhost`
   * Port: `5432`
   * Username: `postgres`
   * Password: (your postgres password)
5. Click **Save**

### 2. Create Database

1. Expand **Servers → PostgreSQL → Databases**
2. Right-click **Databases** → Click **Create → Database**
3. Enter:
   * Database name: `sqat_db`
   * Owner: `postgres`
4. Click **Save**

### 3. Connection String

```
postgresql://postgres:{password}@localhost:5432/sqat_db
```

> <span style="color: #E9D502">Notes</span>

* Use strong passwords in production
* Store credentials in `.env` files
* Avoid using `postgres` user in applications


## 4. Setup Environment Variables

Rename the example file:

```bash
cp .env.example .env
```

Now update `.env` with your database credentials.

## 5. Update `.env`

Example:

```env
DATABASE_URL="postgresql://authuser:password@localhost:5432/authdb"
JWT_SECRET="your_secret_here"
BCRYPT_SALT_ROUNDS="10"
BCRYPT_PEPPER="your_long_random_secret"
```

- `JWT_SECRET`: signing key for JWT tokens (required)
- `BCRYPT_SALT_ROUNDS`: bcrypt cost factor (default `5`)
- `BCRYPT_PEPPER`: extra secret appended before hashing (recommended)
- JWT session: `3 days` (`expiresIn: "3d"` and cookie max-age set to 3 days)

# Prisma Setup

Run the following commands:

```bash
npx prisma generate
npx prisma studio
```

- `generate`: creates Prisma client
- `studio`: opens DB UI

If you change the Prisma schema later, run:

```bash
npx prisma migrate dev
```

# Run the Project

```bash
npm run dev
```

Open:

http://localhost:3000
