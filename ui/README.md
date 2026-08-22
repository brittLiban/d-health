# Daher Health — clinical UI (UI-002 / UI-003 / UI-004)

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
GET-only path — `/openmrs/ws/rest/v1/patient` — to OpenMRS through Caddy's
already-published HTTPS port (`https://emr.localhost:8443`, ADR-008/ADR-010's
existing subdomain routing — no new port is opened). The browser only ever
talks same-origin to the Vite dev server; the credential never leaves that
Node process.

It also strips every OpenMRS error response down to the fixed `{code,
message}` vocabulary (`docs/UI-001-CLINICAL-UX.md` §20) before it reaches the
browser — UI-001A found that a raw OpenMRS error can carry a full Java stack
trace (§13a.1), and that must never reach a clinical user or even a browser
network tab. **The production route (below) does not yet do this rewrite** —
a known gap, not closed by UI-004.

**This is a development convenience, not the production identity path — and
it is deliberately kept, not replaced.** UI-001A left production
`UNRESOLVED` (§15a); UI-003 decided it (`DECISIONS.md` ADR-027,
`docs/UI-001-CLINICAL-UX.md` §15b): no BFF, ever, authenticating as a shared
account — the browser holds the real OpenMRS session directly. UI-004 built
that production path (below) *alongside* this dev proxy, not instead of it:
`SSO_ENABLED=false` remains the local default, so `npm run dev` still needs
something to authenticate with OpenMRS locally. `src/api/client.ts` fetches
the exact same relative path — `/openmrs/ws/rest/v1/patient` — in both
environments; only the mechanism behind that path differs, and only this
dev-only file ever sees a credential.

The production build (`npm run build` / the `Dockerfile`) does **not**
include this proxy at all — it is dev-only code, and the shipped container is
static files served by nginx with nothing that could reach OpenMRS on its
own. Reaching OpenMRS in production is Caddy's job, not this container's —
see "Production routing" below.

## Production routing (UI-004, ADR-027)

`caddy/Caddyfile` now has an `app.$BASE_DOMAIN` origin that:

- Serves this container's static build at `/`.
- Reverse-proxies `/openmrs/*` to `openmrs-gateway:80` — the same prefix
  OpenMRS's own session cookie is scoped to (`Path=/openmrs`), so a session
  established via a real OIDC login lands on this origin and this app's own
  `fetch('/openmrs/...')` calls use it automatically. No credential, no
  token, and no cookie value is ever known to this app's own JavaScript.
- Hardens that cookie in transit (`Secure`, `SameSite=Lax` — see the ADR and
  the Caddyfile's own comments for why `Lax`, not `Strict`, is the value
  compatible with a redirect-based OIDC login) without touching `emr.
  $BASE_DOMAIN`'s native, unhardened cookie.
- Rejects `POST`/`PUT`/`PATCH`/`DELETE` under `/openmrs/*` unless the request
  carries `X-Daher-App-Request: 1` — a header a plain cross-site HTML form
  cannot set, and that CORS (deliberately not relaxed here) stops a foreign
  script from setting either. Defense-in-depth, not authorization: OpenMRS's
  own privilege checks are still the actual authorization boundary.

`OpenELIS` is **not** under `app.$BASE_DOMAIN`. Its SAML SP's ACS-URL
derivation (fixed to `lab.$BASE_DOMAIN`, or dynamic like OpenMRS's OIDC) is
**unresolved** — the underlying `spring-security-saml2-service-provider`
library defaults to per-request derivation and no fixed-URL property exists
in its config schema, but this could not be confirmed live without enabling
SAML on the running stack, which UI-004 was not permitted to do. Until
resolved, OpenELIS-touching actions stay native at `lab.$BASE_DOMAIN` — see
the ADR for the exact fallback.

**Still not done by this route:** no clinical write is enabled anywhere in
this app; the error-body stack-trace rewrite the dev proxy does is not
mirrored here; `SSO_ENABLED` stays `false`, so nothing served at `app.` can
demonstrate a real authenticated session end-to-end yet — that is BLOCKER-5,
not a UI-004 gap.

## PHI storage

No clinical data is written to `localStorage`, `sessionStorage`, or
IndexedDB, per `docs/UI-001-CLINICAL-UX.md` §17a's correction to UI-001:
currently viewed patient data lives in React state only. A browser refresh
re-fetches from scratch rather than restoring from a persistent cache.

## What's not production-ready yet

- **Identity.** No real staff identity propagates through this UI yet. The
  production route (`app.$BASE_DOMAIN`) is real infrastructure, but nothing
  can authenticate through it end-to-end until `SSO_ENABLED=true` and
  BLOCKER-5's role mapping are both resolved — neither of which any UI
  ticket may do unilaterally. In development, every request still goes
  through the dev-only proxy's shared admin credential.
- **Writes.** Nothing in this ticket performs a clinical mutation. "Nouveau
  patient" is visible but disabled, not wired to the runtime-verified
  registration endpoint from UI-001A — enabling it is a deliberate future
  decision once the identity path above is resolved.
- **Routing / domain integration.** `app.$BASE_DOMAIN` now exists (UI-004,
  see "Production routing" above) for the OpenMRS leg only. OpenELIS is not
  under it — its SAML ACS derivation is unresolved, so it stays native at
  `lab.$BASE_DOMAIN` per ADR-027's fallback until that's proven safe.
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
