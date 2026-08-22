# Daher Health — clinical UI (UI-002)

The first real Daher clinical frontend: an app shell, a small design system, and
a functional, French-first, **read-only** patient search. Everything else
(registration, admissions, bed assignment, lab orders, result entry) is
deliberately not built yet — see `docs/UI-001-CLINICAL-UX.md` for the full
product/architecture spec this implements, and its `§13a` for the exact
runtime-verified OpenMRS contracts this code relies on.

## Stack

Vite + React + TypeScript. `npm` with a committed lockfile. Vitest +
React Testing Library + `jest-axe` for tests. No CDN runtime script, no
external font service, no analytics SDK. CSS Modules for styling — no
component-framework dependency.

## Running it

```bash
npm install
npm run dev
```

Opens on `http://localhost:5173`. Patient search calls a dev-only proxy (see
below) that reaches the real OpenMRS instance already running via
`docker compose up -d` in the repo root — the clinical stack must be up for
search to return anything.

## How patient search reaches OpenMRS in development — and why it's not the production path

SSO is off in this deployment today (`docs/ARCHITECTURE.md` §5,
`SSO_ENABLED=false`) — OpenMRS only accepts Basic Auth with the local admin
credential right now. That credential must never reach the browser: not in a
fetch header, not in the bundle, not in devtools' network tab
(`docs/UI-001-CLINICAL-UX.md` §8 of the UI-002 ticket).

`vite.devOpenmrsProxy.ts` is a Vite plugin that runs only in the dev server's
own Node process. It reads `OMRS_ADMIN_PASSWORD` from the repository's root
`.env` (via `envDir` in `vite.config.ts` — no second `.env` file under `ui/`)
and attaches Basic Auth itself when forwarding exactly one whitelisted,
GET-only path (`/api/openmrs/patient`) to OpenMRS through Caddy's already
-published HTTPS port (`https://emr.localhost:8443`, ADR-008/ADR-010's
existing subdomain routing — no new port is opened). The browser only ever
talks same-origin to the Vite dev server; the credential never leaves that
Node process.

It also strips every OpenMRS error response down to the fixed `{code,
message}` vocabulary (`docs/UI-001-CLINICAL-UX.md` §20) before it reaches the
browser — UI-001A found that a raw OpenMRS error can carry a full Java stack
trace (§13a.1), and that must never reach a clinical user or even a browser
network tab.

**This is a development convenience, not the production identity path.**
UI-001A left that path `UNRESOLVED` (§15a); UI-003 has since decided it
(`DECISIONS.md` ADR-027, `docs/UI-001-CLINICAL-UX.md` §15b): no BFF, ever,
authenticating as a shared account — the browser holds the real OpenMRS and
OpenELIS sessions directly, same-origin under a future `app.$BASE_DOMAIN`
that proxies each backend's own API prefix, not this dev-only proxy's shared
credential. See "Next integration step" below for exactly what that requires.

The production build (`npm run build` / the `Dockerfile`) does **not**
include this proxy at all — it is dev-only code, and the shipped container is
static files served by nginx with nothing that could reach OpenMRS.

## PHI storage

No clinical data is written to `localStorage`, `sessionStorage`, or
IndexedDB, per `docs/UI-001-CLINICAL-UX.md` §17a's correction to UI-001:
currently viewed patient data lives in React state only. A browser refresh
re-fetches from scratch rather than restoring from a persistent cache.

## What's not production-ready yet

- **Identity.** No real staff identity propagates through this UI. Every
  request in development goes through the dev-only proxy's shared admin
  credential; there is no per-user session yet.
- **Writes.** Nothing in this ticket performs a clinical mutation. "Nouveau
  patient" is visible but disabled, not wired to the runtime-verified
  registration endpoint from UI-001A — enabling it is a deliberate future
  decision once the identity path above is resolved.
- **Routing / domain integration.** This app is not wired into Caddy or any
  subdomain yet. It runs standalone (`npm run dev` or its own container) by
  design. **Next integration step, per ADR-027:** add an `app.$BASE_DOMAIN`
  block to `caddy/Caddyfile` that serves this container's static build *and*
  reverse-proxies `/openmrs/*` → `openmrs-gateway:80` and
  `/OpenELIS-Global/*` → `openelis-webapp:8443` (the same API prefixes the
  native session cookies are scoped to — not the native SPAs at `emr.`/`lab.`,
  which stay untouched); add `https://app.$BASE_DOMAIN/*` to the `openmrs`
  Keycloak client's `redirectUris`/`webOrigins` in `reconcile-sso.sh` (verify
  the same is safe for `openelis` first — its SAML ACS derivation is
  unverified, see ADR-027); then delete this dev-only proxy entirely rather
  than adapt it, since the browser making same-origin calls with its own
  session cookie needs no server-side credential at all.
- **Pagination.** UI-001A did not exercise OpenMRS's pagination envelope
  against a result set large enough to observe it (§13a.1) — this UI assumes
  a single unpaginated result page, which is honest for this environment's
  fixture data but not proven at production patient-volume scale.
- **Grossesse / Travail & Accouchement tabs.** Show an honest "not yet
  available" placeholder — those forms don't exist yet and are reserved to
  the clinical lead's sign-off (`docs/CLINICAL-SIGNOFF.md`).

## Scripts

| Command | What it does |
|---|---|
| `npm run dev` | Dev server with the OpenMRS search proxy |
| `npm run build` | Type-checks, builds the static bundle, then fails if any secret value ends up in it (`scripts/check-no-secrets-in-bundle.mjs`) |
| `npm run test` | Runs the Vitest suite once (CI mode) |
| `npm run test:watch` | Vitest in watch mode |
| `npm run lint` | `oxlint` |
| `npm run typecheck` | `tsc -b --noEmit` |
| `docker build -t daher-ui .` | Builds the static production container |
