# book2audio frontend

Next.js (App Router) frontend for book2audio, built with shadcn/ui (base-ui style).
Follows a BFF pattern: the browser never talks to the FastAPI backend directly —
every protected call goes through this app's own `/api/*` Route Handlers, which
hold the auth tokens in httpOnly cookies and proxy to the backend server-side.

## Local development

```bash
npm install
cp .env.local.example .env.local   # set BACKEND_API_URL
npm run dev
```

## Deployment (Vercel + Render)

- Set `BACKEND_API_URL` as a **server-side** environment variable in Vercel
  (never `NEXT_PUBLIC_BACKEND_API_URL` — it must not reach the browser bundle).
  It should point at the deployed FastAPI backend's base URL.
- The backend's `CORS_ORIGINS` env var does **not** need to include the Vercel
  domain. This app's browser code never calls the FastAPI backend directly —
  all protected requests are proxied server-side through this app's own
  `/api/backend/*` Route Handlers (see `lib/server/backend-client.ts`), so no
  cross-origin browser request to the backend ever happens. `CORS_ORIGINS`
  only needs to cover origins that call the backend directly (if any).
- `middleware.ts` only checks for the *presence* of the access-token cookie
  to redirect unauthenticated users — it is a UX convenience, not the real
  access-control boundary. Real enforcement happens server-side, when
  `backend-client.ts` sends the token to FastAPI and lets it verify/refresh.

## Directory layout

No `src/` directory — the shadcn `next` template scaffold this app was
initialized from puts `app/`, `components/`, `lib/`, `hooks/` at the project
root (path alias `@/*` maps to the project root, see `tsconfig.json`).
