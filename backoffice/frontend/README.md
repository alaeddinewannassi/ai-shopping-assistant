# Backoffice frontend

The admin dashboard SPA (specs/002-backoffice-analytics Phase 6) — Vite + React + TypeScript
+ TanStack Query, the same toolchain family as `chatbot/widget/`. No Tailwind or Recharts:
plain CSS custom properties carry the design tokens (see `src/theme.css`), and the one chart
(the conversion funnel) is a hand-rolled flexbox bar per the dataviz skill's own rule against
double-encoding a single series with both length and color.

Talks only to `../backend/`'s HTTP API (`src/lib/api.ts`) — never directly to `tenancy-db`
or the chatbot service.

## Setup

```bash
npm install
cp .env.example .env   # VITE_API_BASE, default http://localhost:8001
npm run dev             # http://localhost:5173
```

Requires `../backend/` running (see its own README) and a logged-in session — there's no
public signup; an admin account is created via `../backend/scripts/bootstrap.py` or by
another admin inviting one (once T508 exists — see plan.md).

`VITE_API_BASE` is inlined at **build** time (a Vite/static-site constraint) — a Docker
image built with one API URL baked in needs a rebuild (`--build-arg VITE_API_BASE=...`) to
point at a different backend, it can't be reconfigured at container-run time.

## Scripts

```bash
npm run build   # tsc --noEmit && vite build
npm test        # vitest run
npm run lint    # eslint
npm run format  # prettier --check
```

## Pages

Overview, Funnel, Sessions (list + event replay), Settings (adapter/LLM config, widget
keys + embed snippet, promo rules), and an `/admin/tenants` page for superadmins. Not
built: Confirmations, Commerce, Quality, Cost pages, and user-management/retention
settings — each traces back to a backend-side gap recorded in
`specs/002-backoffice-analytics/plan.md`'s Phase 3-5 notes.
