# DH-011H2A — OpenMRS order-number privilege boundary

**Date:** 2026-08-31  
**Scope:** pinned OpenMRS core 2.8.7, `webservices.rest` 3.5.0, and a fresh disposable
OpenMRS/MariaDB stack  
**Result:** **BLOCKED — the requested privilege exclusion is not true of the upstream baseline,
and the exact REST CREATE response requires another unapproved privilege**

## Role change in this pass

`Daher Clinician Admission` now contains its original nine admission privileges plus exactly the
five separately approved order-path additions: `Add Orders`, `Get Concepts`, `Get Care Settings`,
`Get Providers`, and `Get Order Types`. `Get Global Properties` and `Get Orders` were not added to
that Daher role. Reception is unchanged; no lab-tech role was added.

There is an upstream baseline caveat that the role CSV alone cannot express: OpenMRS core's
built-in `Authenticated` role is considered by `UserContext.hasPrivilege()` for every authenticated
user, whether or not that role appears in `user_role`. In the disposable database, `Authenticated`
already held `Get Global Properties`. Therefore the clinician did not receive that privilege from
the Daher role but did possess it effectively. A direct clinician GET of
`/ws/rest/v1/systemsetting/order.orderNumberGeneratorBeanId` returned `200` and the property value.
This makes the requested state — an ordinary authenticated clinician without effective
`Get Global Properties` — impossible without changing the shared upstream `Authenticated` role.
That broader role change is not approved and was not made in repository configuration.

For the required negative control only, the privilege row was temporarily removed from the
**disposable** database and the process restarted. The direct GET then returned `403`, proving both
the effective baseline grant and the validity of the control. The disposable database was later
destroyed.

## Cold and warm source behavior

The pinned source chain is:

1. `OrderServiceImpl.saveOrderInternal()` assigns a number only for a new order whose
   `orderNumber` is null.
2. It calls the private `getOrderNumberGenerator()`.
3. That method reads `order.orderNumberGeneratorBeanId` through
   `AdministrationService.getGlobalProperty()` only while the static
   `orderNumberGenerator` field is null.
4. `AdministrationService.getGlobalProperty()` is annotated with `Get Global Properties`.
5. With no configured alternate bean, the service caches itself as the generator and allocates
   the seed through the proxied `OrderService.getNextOrderNumberSeedSequenceValue()`, authorized
   by `Add Orders`.

Consequently `Get Global Properties` is checked on generator cache misses, not on every
server-numbered order. A JVM/process restart returns the static field to null. A supported change
or deletion notification for exactly `order.orderNumberGeneratorBeanId` also calls
`setOrderNumberGenerator(null)`. Normal warm orders reuse the cached instance.

There is no core startup invariant that warms this field before the first user order. Startup
ordering, module activity, per-process state, restarts, property-change notifications, and replica
races can all reintroduce the cold path. Prewarming is therefore history-dependent and unsafe.

## `Get Global Properties` scope and current exposure

The exact 2.8.7 `AdministrationService` applies `Get Global Properties` to single-name reads,
the default-value overload, object/UUID reads, prefix and suffix queries, and the all-properties
query. `SystemSettingResource1_9` exposes the same broad REST resource; its default/full
representations include the value, not merely the name. Write and purge operations use separate
privileges.

The disposable Daher-derived database contained 358 global properties. Seventy-nine property
names matched a conservative security/integration-name filter. No non-null property values were
printed or recorded. Safe category counts included:

- 10 `mail.*`, including names for host, username, and password;
- 15 `fhir2.*` and 8 `webservices.*`;
- 7 `openconceptlab.*`, including a token-named property;
- 3 `oauth2login.*`;
- 2 `scheduler.*`, including username/password names; and
- host/login URL names plus other module endpoints, topology, identifiers, and integration
  configuration.

Granting this privilege materially broadens configuration visibility: **YES**. More precisely,
the upstream `Authenticated` baseline already creates that exposure for every authenticated user
in this stack. Removing it is a separate scope-wide authorization decision, not a clinician-role
subtraction.

## Disposable cold/warm/restart proof

The first fresh-stack attempt, before the baseline inheritance was noticed, returned `500` but
increased native `orders` from 0 to 1. That was not an order-number failure: the inherited
`Authenticated` privilege allowed the cold generator read; the row committed; response conversion
then failed on a separate `Get Orders` gate described below.

The controlled proof temporarily removed `Get Global Properties` from `Authenticated` in the
disposable database and restarted:

| Control | HTTP result | Native `orders` delta | Exact result |
|---|---:|---:|---|
| Direct clinician system-setting read | 403 | N/A | `Privileges required: Get Global Properties` |
| True cold clinician order | 403 | 0 | failure at `OrderServiceImpl.getOrderNumberGenerator()` → `getGlobalProperty()` |
| Administrator warm-up order | 201 | +1 | generator cache populated |
| Warm clinician order | 500 | +1 | order committed; response conversion failed without `Get Orders` |
| Restart, then clinician order | 403 | 0 | cold `Get Global Properties` failure returned |

The warm diagnostic is not a success contract: it demonstrates cache sensitivity and a dangerous
commit-then-error response, not a deployable solution.

## Additional mandatory stop: `Get Orders` after commit

Pinned `webservices.rest` source shows `DelegatingCrudResource.create()` saves the delegate before
converting it to the requested response representation. `ConversionUtil` checks the Order
resource's `getRequiredGetPrivilege()`; `OrderResource1_8` returns `Get Orders`. Without it,
conversion returns a sentinel `Object`, which `DelegatingCrudResource.create()` casts to
`SimpleObject`, causing the observed `ClassCastException` and HTTP 500 **after the transaction has
committed**.

`Get Orders` was not approved, was not added, and was not temporarily proxied. It is a broader
clinical-data read privilege. The exact supported generic REST CREATE endpoint therefore still
does not give the clinician an honest successful response even after the order-number issue is
handled. This also corrects the earlier H2 audit's statement that response rendering added no
authorization check.

## Client-supplied `orderNumber`

The REST resource lists `orderNumber` as creatable, but the domain setter permits a non-null manual
number only when `order.allowSettingOrderNumber=true`; the core default is false and core tests
assert that default refusal. Reading that flag uses `ConfigUtil` and itself performs a global
property lookup on a cold cache, so this option does not reliably eliminate the cold privilege
dependency. Once a number is accepted, `saveOrderInternal()` skips generator selection.

The native column is non-null `varchar(50)`. `orders_order_number` is an ordinary index, not a
unique constraint. The number is returned in REST representations, translated to the FHIR placer
identifier, and used by order-number/history lookup APIs. It is therefore user-visible and an
external/correlation identifier, not an inert caller label.

Disposable controls ran only after an administrator enabled the manual-number flag and thereby
warmed its configuration cache. Because the clinician still lacked `Get Orders`, successful
native creates returned the same post-commit HTTP 500; native rows are authoritative:

| Input/control | HTTP result | Native result |
|---|---:|---|
| Manual number while setting disabled/cold | 400 | zero rows; its flag read required `Get Global Properties` |
| Valid `DH2A-VALID-001` | 500 after commit | one exact row |
| Same `DH2A-DUP-001` twice | 500, 500 after commit | two rows with the same number |
| Empty string | 500 after commit | one row with an empty number |
| 51-character number | 400 | zero rows; field-length validation rejected it |
| Five concurrent identical numbers | five 500s after commit | five rows with the same number |
| Five concurrent distinct numbers | five 500s after commit | five distinct rows |
| Restart with manual flag still true, then supplied number | 400 | zero rows; cold flag lookup required `Get Global Properties` |

Classification: **UNSAFE**. It weakens server ownership, has no uniqueness invariant, accepts an
empty identifier, can create concurrent collisions, and still has a cold global-property read.

## Narrow server-side seam

A narrow seam is technically feasible but requires architecture approval and new code. The
smallest privilege elevation in source would surround only the exact
`AdministrationService.getGlobalProperty(GP_ORDER_NUMBER_GENERATOR_BEAN_ID)` call with
`Context.addProxyPrivilege(Get Global Properties)` and `removeProxyPrivilege(...)` in `finally`.
It would return no property data, preserve the configured native generator and standard order
validation, and leave the authenticated/audited actor as the clinician. OpenMRS documents this
proxy mechanism for internal reads that must not expose the protected data.

There is no existing public supported seam that scopes the elevation that narrowly. A module
endpoint wrapping the whole `saveOrder()` call would hold the proxy privilege over more code than
the one read. A custom generator does not avoid the initial bean-id property lookup. Reflection or
cache manipulation is not acceptable. The exact narrow change therefore implies maintaining a
pinned core patch or an equivalently precise, reviewed interception point.

The generic REST response's separate `Get Orders` requirement also needs an approved design. One
possible reviewed composition is: the exact internal generator read seam plus a Daher-owned,
`Add Orders`-protected create endpoint that invokes standard validation/save and returns only the
created UUID/status without using the generic Order read representation. It must not become a
generic privileged proxy or expose arbitrary orders/global properties. This is a proposal, not an
implementation or approval.

Audit identity remains the authenticated clinician because proxy privileges augment the current
`UserContext`; they do not substitute a system user. Any implementation must prove privilege
removal on success and exception, native server numbering, validation parity, non-disclosure, and
the original actor in audit fields.

## Options

| Option | Clinician gets `Get Global Properties`? | Preserves native generation? | New code? | Security risk | Operational risk | Recommendation |
|---|---:|---:|---:|---|---|---|
| Explicitly grant it | Yes (and redundant while `Authenticated` already grants it) | Yes | No | Broad configuration values readable | Low, but obscures baseline exposure | Reject |
| Caller-supplied `orderNumber` | Cold flag lookup still needs it; warm path can skip generator | No | Adapter work | Collisions, empty values, caller ownership | High; cache-sensitive | Reject / unsafe |
| Remove baseline grant + exact server-side seam | No | Yes | Yes | Smallest if exact read only; must be reviewed | Moderate pinned-core/endpoint maintenance | Preferred candidate, human decision required |
| Cache prewarm | No on warm requests only | Yes | Yes | Privilege depends on process history | High across restart/reset/replicas/races | Reject / unsafe |

## Recommendation and stop

Do not add `Get Global Properties` or `Get Orders` to the Daher clinician role. Do not adopt
caller numbering or cache prewarming. Preserve native server-generated order numbers.

Architecture review is required for two coupled changes before implementation:

1. whether to remove `Get Global Properties` from the shared upstream `Authenticated` role, which
   changes authorization for every authenticated user; and
2. whether to build the exact internal generator-read seam and a narrow create response that
   avoids granting broad `Get Orders` while preserving standard validation and clinician audit
   identity.

No privilege or architecture expansion was made. ADR-037's exact Provider-UUID practitioner
mapping remains accepted and unimplemented. DH-011I evidence is unchanged. No poller, scheduler,
DH-011L flow, production composition, or real-facility operation ran.

## Verification and cleanup

- Role suite: 9 passed.
- Existing seed/mapping suite: 25 passed.
- Negative controls: removing `Get Providers` caused exactly the exact-set and sole-holder tests
  to fail; removing `Get Order Types` caused those same two targeted tests to fail.
- Workflow YAML parse, role CSV parse, shell syntax, focused secret-like assignment scan, and
  `git diff --check`: clean.
- Full CI was not run.
- The disposable OpenMRS/MariaDB containers, two volumes, network, pinned source clones, scripts,
  and response artifacts were removed and verified absent. The locally rebuildable OpenMRS image
  was not created by this pass and was not removed.
