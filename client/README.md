# SQAT Client

This is the Next.js frontend for the SQAT automation platform.

## Prerequisites

- Node.js 20+
- Backend running at `http://localhost:8000`

## Setup

```powershell
npm install
copy .env.example .env
```

Configure:

```env
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

## Run Locally

```powershell
npm run dev
```

Open:

```text
http://localhost:3000
```

## Checks

```powershell
npm run lint
npm run build
```

## Notes

- Database setup belongs to the backend.
- Do not commit `client/.env`.
- Do not commit generated build folders such as `.next/`, `out/`, or `dist/`.
