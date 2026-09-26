# Native security qualification — what was exercised, and what it does not prove

**DHEALTH-NATIVE-SEC-001, 2026-09-26.** A real Chromium, a real Caddy edge, a real Keycloak 26.7.1,
the real Exchange adapter and a real PostgreSQL 16, asked the hostile questions: expired tokens,
wrong audiences, disabled accounts, withdrawn memberships and roles, cross-tenant reads, other
people's patient grants, unpublished routes, logout, and what ends up in the logs.

**The claim this earns, in full.** *Key Native security boundaries have been exercised against the
real runtime, including deliberate negative controls that were observed to fail and then reverted.*

**What it is not.** Not a penetration test. Not an independent audit. Not a compliance
certification of any kind. Not evidence about a production deployment, a public certificate
authority, a real domain, or real patient data. `BASE_DOMAIN=localhost`, Caddy's internal CA, one
workstation, synthetic data throughout.

---

## 1. Run it

```bash
scripts/demo/up.sh                  # start the native runtime and seed the baseline
scripts/demo/qualify-security.sh    # 31 security tests
scripts/demo/reset.sh               # restore the baseline before running it again
```

The runner refuses to start until the edge genuinely answers — a **401 from
`/exchange/v1/me` plus a 200 from Keycloak's discovery document**, not merely "some response". This
matters more than it looks: Caddy answers **502** for roughly half a minute after the adapter or
Keycloak restarts, and a 502 satisfies a naive `expect([401, 403, 404]).toContain(status)`
assertion just as well as a real refusal does. "The boundary refused me" and "the upstream was not
listening" must never be the same observation, so every `describe` block re-checks readiness rather
than trusting the previous one.

Two environment notes, neither a DHealth defect:

- **`app.localhost`, `id.localhost` and `exchange.localhost` must resolve to 127.0.0.1.** Chromium
  special-cases the `.localhost` TLD; **Node's resolver does not**, so the suite's API request
  contexts fail with `ENOTFOUND` even while the browser works fine. On WSL, `/etc/hosts` is
  regenerated on every boot unless `generateHosts = false` is set in `/etc/wsl.conf`.
- **Node's global `fetch` does not trust Caddy's internal CA.** The runner exports Caddy's own root
  and points `NODE_EXTRA_CA_CERTS` at it. It deliberately does **not** set
  `NODE_TLS_REJECT_UNAUTHORIZED=0`, which would be one line and would silently void every TLS
  assertion in the suite.

## 2. The security model, as the code actually implements it

**Authority = organization scope + role/capability scope + resource relationship.** All three are
evaluated server-side, per request.

- `exchange/identity_auth.py` turns an `Authorization` header into a `ValidatedToken` or **one**
  `TokenValidationError`. Issuer, audience, signature, `exp` and `nbf` are all delegated to PyJWT;
  `exp`, `iat` and `sub` are required; RS256 only, and the constructor refuses `none` outright.
  Every failure mode collapses to the same 401 with the same body, so a caller cannot use the
  response as a validation oracle.
- `exchange/authorization.py` is the only place organization-scoped decisions are made. Every
  `require_*` performs a **live database read** of membership and roles. Nothing is trusted from
  the token's claims except who the caller is.
- Two refusal shapes, deliberately: **404** when the identity has no relationship to the
  organization at all (indistinguishable from the organization not existing — anti-enumeration),
  **403** when they legitimately belong but lack the specific permission.
- **Administrative authority is not clinical authority.** `require_clinician`, `require_lab_tech`
  and `require_lab_result_releaser` each refuse a platform admin who lacks the role, and each
  refuse an `ORG_ADMIN` who holds only that. ADR-044's separation of entry from release is enforced
  the same way.
- The browser is never the boundary. `ui/src/features/workspace/workspaceCapabilities.ts` hides
  what a user cannot do; the server refuses regardless of what was rendered.

**Threat model, narrow and current.** Unauthenticated internet caller; authenticated clinician;
lab technician; result releaser; org admin; platform admin; a holder of one order-scoped patient
grant; and a holder of a still-valid token whose authority has just been withdrawn. No new role
taxonomy was invented for this ticket.

## 3. What was observed

| # | Scenario | Result |
|---|---|---|
| SEC-1 | Unauthenticated staff route | Redirected to real Keycloak; the authorization request carries `code_challenge_method=S256` |
| SEC-2 | Real Authorization Code + PKCE login | Accepted; `iss`/`azp`/`aud` are the real realm, client and API audience |
| SEC-3 | Real Keycloak token, wrong audience | **401**, generic body |
| SEC-4a | Real token, genuinely expired | **401** after real clock expiry (per-client 15s lifespan; realm default untouched) |
| SEC-4b | Keycloak session terminated server-side, then the app reloaded | The next load does **not** restore an authenticated workspace — `check-sso` asks the real Keycloak, finds no session, and the browser ends at the login form. **The in-page `updateToken` failure path was NOT exercised** — see the note under this table |
| SEC-5 | Disabled Keycloak account | Cannot establish a new session; never reaches a workspace |
| SEC-6 | Membership withdrawn, **same still-valid token** | Next request refused; `/me` reports the membership as non-ACTIVE |
| SEC-7 | `LAB_RESULT_RELEASER` removed, same valid token | Release surface refused on the next request |
| SEC-9 | Role **added** mid-session | Visible to the server immediately — the model is symmetric |
| SEC-8/10 | Cross-tenant read; fabricated organization id | Refused; no stack trace, no SQL |
| SEC-11 | Two grants, two orders, same patient | Each opens only its own order; a grant authorizes nothing on the staff API |
| SEC-12 | Revoked grant; malformed grant; random grant | All refused, all identically |
| SEC-13 | Patient credential in the URL fragment | Scrubbed from the address bar; never appears in any request URL |
| SEC-14/15 | Internal health, docs, metrics, Connect, notification prefix | All **404 at the edge with a zero-byte body** — the request never reaches the adapter |
| SEC-16 | Logout | No usable access afterwards; a protected route returns to Keycloak |
| SEC-17b | A **real** platform-admin token (`dhealth-platform-admin` realm role, zero memberships) | **403** on provider orders, the lab work queue and the release queue — network administration is not clinical authority, and the shape is 403 not 404 because a platform admin already knows the organization exists |
| SEC-20 | A 403 refusal (lab technician asking a provider surface of their own organization) | Appended to the file-backed refusal audit. **A 404 is deliberately not audited** — see below |
| SEC-17 | Response cache policy | Patient responses are `no-store`; **staff clinical responses carry none** (finding G2) |
| SEC-18 | Credentials in logs | No JWT, no `Bearer`, no password, no clinical value, no patient name in Caddy, adapter or Keycloak logs |

**What SEC-4b does not cover, stated because the stronger claim is the tempting one.** The in-page
refresh-failure path — `createExchangeTokenProvider`'s `updateToken` rejecting, firing
`onAuthRefreshError`, and `ExchangeAuthProvider` setting its truthful *"La session DHealth Exchange
a expiré"* state — was **not** exercised. Reaching it needs either a page held open past the realm's
300s access-token lifetime, or a hook into the `keycloak-js` instance that the product does not
expose and that this qualification will not manufacture, because fabricating auth state is exactly
what it forbids itself. The path exists and is unit-tested; it has not been seen in a real browser.

**The 403-vs-404 audit distinction, found by SEC-20.** `_audit_refused` is called on
`NotAuthorized` and **not** on `ResourceNotFound`. So a 403 — a known identity, refused a specific
permission on an organization it genuinely belongs to — is audited, while the anti-enumeration 404
is not. That is defensible (there is no meaningful actor/resource pair in a probe of a fabricated
UUID, and auditing every such probe would be a denial-of-service against the audit volume) and it
is written down here because the first draft of SEC-20 assumed the opposite and went green for the
wrong reason.

**Effective headers at the edge, on all three vhosts:** `Strict-Transport-Security:
max-age=31536000; includeSubDomains`, `X-Content-Type-Options: nosniff`, `Referrer-Policy:
same-origin`, `X-Frame-Options: SAMEORIGIN`, and no `Server` banner. Keycloak sets its own
`Content-Security-Policy: frame-src 'self'; frame-ancestors 'self'; object-src 'none'` and the edge
does not override it. `checkLoginIframe: false` in `ExchangeAuthProvider` means the app never frames
the identity host, so the two framing policies cannot conflict.

**CORS:** none, and that is the correct result. Native is same-origin by construction —
`app.<domain>` serves the UI *and* proxies `/exchange/v1/*` to the adapter in the same vhost — so no
cross-origin grant is needed. An attacker `Origin` is echoed nothing, on the request or the
preflight.

**CSRF:** not applicable to the Exchange API, which is bearer-authenticated and reads no ambient
cookie; a cross-site form post carries no `Authorization` header and is refused as unauthenticated.
Keycloak's own cookie-authenticated login flow is Keycloak's to defend, and it sets
`Secure; HttpOnly; SameSite=Strict` plus its own state checker. No generic CSRF middleware was
added, because adding one to a bearer API would be ceremony rather than a control.

**Keycloak realm, as configured:** brute-force protection **on** (`failureFactor` 10, wait 60s
rising to 900s, no permanent lockout), `registrationAllowed` false, `rememberMe` false,
`resetPasswordAllowed` true, `accessTokenLifespan` 300s, `ssoSessionIdleTimeout` 1800s,
`ssoSessionMaxLifespan` 14400s. **No password policy is configured** and no MFA — both recorded
below rather than changed here.

## 4. Negative controls — executed, observed red, reverted

A test that would pass regardless of whether the code is right proves nothing. Each control below
broke one real production guard, ran the suite, and was reverted.

| Control | What was broken | What went red |
|---|---|---|
| NC-SEC-1 | `OrganizationMembershipRepository.find_active` stopped filtering by `organization_id` — the one function every `require_*` calls | SEC-8/SEC-10: the cross-tenant read **answered 200**. A clinician of the demo clinic could read the laboratory's orders |
| NC-SEC-2 | The `LAB_RESULT_RELEASER` check in `require_lab_result_releaser` bypassed | SEC-7: the release queue **answered 200** for an identity whose role had been removed |
| NC-SEC-3 | `validate_token`'s `token_hash` binding removed, so any credential resolves to a grant | SEC-11/SEC-12: a **revoked** grant answered 200 — one patient's credential opened another's live grant |
| NC-SEC-4 | `/health/ready` published on the `exchange.` vhost | SEC-14: expected 404, **received 200** |
| NC-SEC-5 | `no-store` removed from the patient order response | SEC-11's cache assertion: expected `no-store`, received `undefined` |
| NC-SEC-7 | `X-Frame-Options` removed from the edge's shared header block | SEC-EDGE-5: expected `SAMEORIGIN`, received `undefined` |

**One control proved nothing, and that is worth recording.** The first NC-SEC-3 attempt mutated a
`select(PatientAccessGrant)` inside `_issue_in_session`'s collision-retry path rather than the one
in `validate_token`. Nothing went red — correctly, because the guard under test was untouched.
Mutating the wrong function is exactly how a negative control quietly proves nothing; the same trap
caught DHEALTH-NATIVE-MVP-1.0R's first NC-3 attempt.

**NC-SEC-6 (logout) was not run.** The narrowest meaningful mutation is a frontend change to
`ExchangeAuthProvider.logout`, which needs a UI image rebuild; the five backend and edge controls
above already cover the boundaries that carry clinical consequence. Recorded as owed rather than
quietly omitted.

## 5. Findings

| ID | Severity | Finding |
|---|---|---|
| **F1** | **P1** — **CLOSED by DHEALTH-NATIVE-ACCESS-001, 2026-09-26** | **Authority can be granted and never withdrawn through the product.** `OrganizationMembershipRepository.set_inactive` and `OrganizationRoleAssignmentRepository.remove` both exist and **neither has a caller** in `api.py` or the UI. `POST .../memberships` grants; nothing revokes. SEC-6 and SEC-7 prove the backend fails closed *given* the withdrawn state — they do **not** prove an administrator can reach it, and the suite produces that state with direct SQL for exactly this reason. **→ See §5.1 below for the disposition.** |
| **G1** | P2 | **SPA fallback path confusion.** `app.<domain>`'s catch-all returns **200 text/html** for `/health/ready`, `/notifications/v1/*`, `/api/*`, `/admin/*` — the SPA shell, byte-identical to `/`. No data is disclosed; the harm is that a misconfigured callback, probe or monitor looks healthy. This is the class of defect DH-NOTIFY-META-001R already found once. The `exchange.` vhost is correctly a bare 404. Pinned by SEC-EDGE-4. |
| **G2** | P2 | **No cache policy on authenticated staff clinical responses.** `no-store` is set on exactly eight handlers, all patient-access/notification. Orders, results, release versions, the review queue and the patient directory send no cache directive at all. Pinned by SEC-17. |
| **G3** | P3 | **Keycloak's admin console is reachable at `id.<domain>/admin/`.** Login-protected, and the console is how this deployment is administered, so restricting it is a supportability decision rather than a bug fix. |
| **G4** | P3 | **No `Content-Security-Policy` and no `Permissions-Policy`** on the application origin. Writing a correct CSP requires a resource inventory this ticket did not do; guessing one was explicitly out of scope. Pinned by SEC-EDGE-7. |
| **G5** | P3 | **No password policy configured** in the realm, and no MFA. Brute-force protection is on and is the current compensating control. |
| **H1** | — | **Harness bug, fixed here:** `ui/e2e/support/adminApi.ts`'s `createSetupClient` creates a client with **no audience mapper**, so tokens minted from it would be refused for a reason unrelated to whatever a test believed it was proving. It survived because DH-CLINIC-004B never executed. `securityAdmin.ts`'s own client adds the mapper. |

### 5.1 F1's disposition — closed by DHEALTH-NATIVE-ACCESS-001

    found in DHEALTH-NATIVE-SEC-001 (2026-09-26)
        ↓
    closed by DHEALTH-NATIVE-ACCESS-001 (2026-09-26)

**The original finding is not edited away, and it was not a backend defect.** What this
qualification proved — that a withdrawn membership and a removed role are refused on the very next
request, against a still-valid token — was correct then and is correct now. What was missing was any
way for a facility administrator to *cause* that state: the two repository methods had no caller, so
SEC-6 and SEC-7 had to reach into PostgreSQL with `psql`, and that is exactly what made it a P1
rather than a note.

**What now exists** (`docs/ARCHITECTURE.md`, `adapter/src/daher_adapter/exchange/staff_access.py`):
four organization-scoped, role-scoped, audited endpoints — deactivate a membership, reactivate it,
add one allowed organization role, remove one — reached by a facility's own administrator on an
`Équipe et accès` screen (`ui/src/features/team/TeamAccessPage.tsx`). Each is one transaction that
locks the organization row, re-checks the actor's own `ORG_ADMIN` *inside* that lock, proves the
target membership belongs to the organization, mutates, and writes its audit row in the same
transaction. Three new audit event types: `membership_deactivated`, `membership_reactivated`,
`role_removed`.

**The closure standard applied.** The P1 was that *a facility administrator could not do this*, so
adding endpoints alone would not have closed it. `ui/e2e/native/07-access-lifecycle.spec.ts` drives
the real screen in a real Chromium: the administrator clicks, reads a confirmation naming the person
and the establishment, confirms, and the colleague's already-signed-in browser — holding a token
issued before the click — is refused on its next request.

**SEC-6 and SEC-7 were deliberately NOT rewritten to use the new API.** They answer a different
question ("does the backend fail closed *given* this state?") from the new suite's ("can an
administrator *reach* that state?"), and neither subsumes the other. Both still run, and the new
suite has its own config and its own report (`scripts/demo/qualify-access.sh`,
`native-access-results.json`) so that this document's "31 security tests" keeps meaning what it says.

**A second, narrower defect this closure uncovered and fixed.** §1 above tells an operator that
`scripts/demo/reset.sh` plus re-running the demo user reconciliation is the recovery for a run killed
with a membership left inactive. That was **false**. `demo_seed`'s `_grant_membership` called
`memberships.create` (insert-first-wins, so a re-seed resolves to the existing INACTIVE row) and then
`roles.assign`, which refuses an inactive membership — so one deactivated membership made `reset.sh`
raise, with no supported way back to the baseline. Reproduced, then fixed by converging the
membership's status, and pinned by `adapter/tests/test_demo_seed.py`'s `test_bs5_...`. It was latent
before (only `psql` could reach it, which is what SEC-6 did); it is ordinary now that withdrawing
access is a supported product action.

**Negative controls for the closure** are recorded in `PROJECT_STATE.md`'s DHEALTH-NATIVE-ACCESS-001
entry rather than in §4 above, so this document's own "six negative controls" count keeps meaning
what it says. They break the new guards specifically: the membership active-state filter, the
clinician role check, the target-organization ownership check, the last-administrator protection and
the organization-type role matrix.

**Still open, and explicitly not touched by that ticket:** G1 (SPA fallback), G2 (staff cache
policy), G3 (Keycloak admin console), G4 (CSP/Permissions-Policy), G5 (password policy/MFA), §6
(successful-read audit policy — `HUMAN DECISION REQUIRED`), §7 (session/timeout policy — `HUMAN
DECISION REQUIRED`), and §8's owed items including SEC-19 (multi-organization context isolation) and
NC-SEC-6. None of those is resolved, and none was quietly marked so.

**One new gap it records rather than fixes:** there is still no way to create a staff member's
*first* membership from inside a facility. A platform administrator does it (`FacilityTeam`), because
DHealth has no invitation or account-creation flow at all — deliberately out of that ticket's scope.
See `PROJECT_STATE.md`.

**One thing that looked like a finding and is not.** `/me` lists a withdrawn membership rather than
omitting it — but it carries `membership_status`, and `isUsableMembership` refuses any membership
whose status or organization status is not `ACTIVE`. The model is coherent. The first draft of
SEC-6 asserted that the membership must vanish entirely, which is a design this product never had;
asserting it would have manufactured a defect rather than found one.

## 6. Successful-read auditing — `HUMAN DECISION REQUIRED`

`exchange/security_audit.py` records **mutations**, transactionally, in the same database
transaction as the change they describe. `exchange/audit.py`'s file-backed writer records
**authorization refusals**, best-effort, and is deliberately wrapped so a failed audit write can
never turn a refusal into an allow.

**Successful sensitive *reads* are not audited at all**, and `SecurityEventType` contains no read
event. No ADR decides whether they should be. The question is not an agent's to settle: it changes
retention, storage volume and privacy posture, and the useful distinction — a clinician opening one
patient's record versus a queue polling every thirty seconds — has not been drawn anywhere in this
repository. Raised, not implemented.

## 7. Session and timeout policy — `HUMAN DECISION REQUIRED`

The realm's current values (300s access token, 30-minute idle, 4-hour maximum) are Keycloak-shaped
defaults carried since DH-EX-008, not a clinical decision about how long a workstation in a busy
clinic should stay signed in. This ticket tested the *behaviour* and deliberately did not choose the
*policy*.

## 8. Owed — evaluated, not done, and not quietly dropped

- **SEC-19, multi-organization context isolation.** Not built. The demo baseline has no identity
  holding memberships in two organizations, so there is no establishment switch to exercise and no
  cross-organization UI race to observe. Creating one is straightforward through the real
  `POST .../memberships` API; it was cut for scope, not because the scenario is uninteresting — a
  stale response from the previous organization rendering under the new organization's header is
  exactly the class of defect that belongs in this suite.
- **NC-SEC-6, the logout negative control.** The narrowest meaningful mutation is a frontend change
  to `ExchangeAuthProvider.logout`, requiring a UI image rebuild. The five backend and edge controls
  already cover the boundaries that carry clinical consequence.
- **The in-page refresh-failure path** (see §3's note under the table).
- **Read auditing and session policy** are §6 and §7 — decisions, not omissions.

## 9. What this qualification does not cover

Production deployment, public certificate issuance, DNS, off-site backup, real Meta/WhatsApp
credentials, any Connect vendor adapter, human usability (DH-USABILITY-001 remains
`INTERNAL WORKFLOW REVIEW — NOT USER-QUALIFIED`), and any claim about real patients. No pilot gate
is created or cleared by this ticket: it strengthens the evidence behind gates 1 and 2, which
DHEALTH-STATE-001 already cleared, and re-testing them is not a new gate.
