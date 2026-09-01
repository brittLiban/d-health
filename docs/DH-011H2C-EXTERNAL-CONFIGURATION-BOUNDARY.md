# DH-011H2C — OpenMRS external configuration and narrow TestOrder boundary

**Date:** 2026-08-31

**Status:** implemented and verified on disposable infrastructure; uncommitted

**Scope:** OpenMRS 2.8.7 / `webservices.rest` 3.5.0 / Daher module

## Pinned-source audit

The audit used OpenMRS core tag `2.8.7`, commit
`7ddf30f4fa772e4efd0b201b3bd8a34546247b0a`, and `webservices.rest` tag `3.5.0`, commit
`69fa31fc157be0b0835e2101a0b0e480e0da4acb`. The Daher UI and server source contain no legitimate
runtime calls to these resources. No global-property value is reproduced here.

| HTTP surface | Method | Exposes names? | Exposes values? | Pinned upstream privilege before the Daher guard | Needed by normal Daher UI? |
|---|---|---:|---:|---|---:|
| `/ws/rest/v1/systemsetting` | GET list | Yes | Yes: even `display` embeds the value; default/full include `value` | `Get Global Properties` | No |
| `/ws/rest/v1/systemsetting?q={prefix}` | GET prefix search | Yes | Yes, by representation | `Get Global Properties` | No |
| `/ws/rest/v1/systemsetting/{uuid-or-property-name}` | GET one; `v=ref/default/full` | Yes | Yes | `Get Global Properties` | No |
| `/ws/rest/v1/localeandthemeconfiguration` | GET | Indirectly | Yes, selected settings | `Get Global Properties` through `AdministrationService` | No |
| `/ws/rest/v1/visitconfiguration` | GET | Indirectly | Yes, selected settings | `Configure Visits` plus protected global-property reads | No |
| `/ws/rest/v1/nametemplate` and `/{codename}` | GET | Yes | Yes, layout configuration | Protected global-property reads through the layout provider | No |
| `/ws/rest/v1/addresstemplate` and `/{codename}` | GET | Yes | Yes, layout configuration | Protected global-property reads through the layout provider | No |
| `/ws/rest/v1/systeminformation` | GET | Yes | Yes, runtime/system details | `View Administration Functions` | No |
| `/openmrs/module/webservices/rest/settings.form` | GET/POST | Yes | Yes, REST module settings | JSP `Manage RESTWS`, then protected global-property reads/writes | No |

No `/globalproperty` REST alias exists in the pinned `webservices.rest` source. The only setting
search implementation is the `systemsetting?q=` prefix search; the core Java API also has suffix
lookup, but the pinned REST module does not publish a suffix-search resource. CRUD writes use the
same protected `systemsetting` path and are also inside the external guard.

## Boundary

OpenMRS's built-in `Authenticated` role retains `Get Global Properties`. Pinned core requires that
compatibility during ordinary `HibernatePatientDAO.findPatients()` execution and native order
number generator selection. The Daher module does not change internal Java service authorization.

Externally, a Spring `MappedInterceptor` protects the exact paths in the table. It requires the
new `Daher View System Configuration` privilege, returns fixed `401`/`403` JSON before a protected
controller runs, and never returns upstream content on denial. An Initializer loader runs after
the roles domain and converges the new privilege to exactly one direct holder:
`Organizational: System Administrator`. It also removes copies absorbed by Initializer's upstream
wildcard roles on later boots. `Authenticated`, `Daher Clinician Admission`, `Daher Reception`,
and lab-tech do not directly hold it.

The first restart proof caught the wildcard absorption defect: direct holders changed from one to
three (`Privilege Level: Full` and `Privilege Level: High` were added). That proof was rejected.
The loader was hardened to remove every non-allowlisted direct copy; two consecutive boots of the
corrected image each converged to exactly the sys-admin role.

## Narrow TestOrder contract

`POST /openmrs/moduleServlet/daherendadmission/daherCreateTestOrder` accepts exactly three strings:
`encounterUuid`, `conceptUuid`, and `careSettingUuid`. There is no approved deterministic
care-setting derivation rule, so the caller still supplies that UUID. The server derives the
encounter-bound patient, the authenticated user's sole active Provider, the fixed native Test
Order type, native order number, `NEW` action, and audit creator.

The clinician must directly hold `Add Orders`. Only four exact internal reads are temporarily
proxied: `Get Providers`, `Get Concepts`, `Get Care Settings`, and `Get Order Types`. Each proxy is
added immediately around one trusted lookup and removed in `finally`; no caller supplies a
privilege name. The response is Daher-owned and contains only `status`, `orderUuid`, and
`orderNumber`, so `Get Orders` is not required.

## Disposable live evidence

- Clinician and reception patient search: `200`; lab-tech: `403` as expected for its zero-role
  OpenMRS fixture.
- Direct configuration reads: clinician/reception/lab-tech `403` with the fixed 22-byte body;
  sys-admin `200` with normal upstream bodies. Collection, query, single-resource, and
  `ref/default/full` forms were exercised. Browser-session proof covered `settings.form`.
- Reception generated a native identifier and created one synthetic patient (`201`); full read-back
  recorded reception as creator. Clinician created the pinned maternity Visit and linked Admission
  Encounter (`201`/`201`).
- First authenticated endpoint use and warm use returned `201`; the database contained native
  `orders` plus `test_order` rows, fixed Test Order type, encounter-bound patient, clinician's own
  Provider, server number, and clinician creator. No generic response serialization failed.
- `patientUuid`, `ordererUuid`, `orderTypeUuid`, `orderNumber`, `action`, `previousOrder`, and a
  non-TestOrder `type` field each returned `400` with zero writes.
- Reception, zero-role lab-tech, clinician-minus-`Add Orders`, zero-Provider clinician, and
  two-Provider clinician each returned `403` with zero writes. A valid-looking generic
  `/ws/rest/v1/order` POST returned `400` with zero writes.
- Sys-admin changed the generator bean setting to a valid registered bean, then reset it to its
  original null state. Subsequent clinician creates returned `201`; clinician setting reads
  remained `403`. A deliberately invalid disposable bean caused fixed `500`/zero writes, and reset
  restored success.
- Concurrent clinician A/B creates both returned `201`; concurrent reception returned `403`.
  Exactly two rows appeared, with the correct distinct creators and Providers. Generic concept
  reads remained `403` afterward.
- A deliberate MariaDB outage returned fixed `500` with zero writes. The same JVM recovered; after
  its pool discarded stale connections, patient search and a later create succeeded. Two corrected
  OpenMRS boots also created orders successfully.
- Existing end-admission regression: reception `403`, clinician `200`; the Visit was stopped and
  not voided.

All fixtures were synthetic and disposable. The test concept was explicitly named as a disposable
technical fixture and is not an OpenMRS-to-OpenELIS clinical mapping. No poll cycle, scheduler,
DH-011L flow, production `DeliverOrder`, OpenELIS practitioner implementation, real staff, or real
facility data was used.

## Remaining boundary

The OpenMRS source-order half is technically closed for review. DH-011H remains incomplete because
ADR-037's exact Provider-UUID mapping into native OpenELIS is accepted but unimplemented. DH-011I
also remains open: its audited registries have zero exact one-to-one LOINC matches, so no production
test mapping may be fabricated.
