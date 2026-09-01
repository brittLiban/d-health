# DH-011H1 OpenELIS practitioner intake spike

**Date:** 2026-08-31  
**Scope:** synthetic/disposable only; no facility stack, real staff, or patient data queried  
**Pinned target:** repository-patched OpenELIS `3.2.1.11` built from upstream commit
`e2a6bdf7999fa3754e7cdafbd2a91259c3f0ce23`

## Result

**The current Option-F push path cannot safely create/find a previously unseen OpenMRS
Provider/FHIR Practitioner.** It creates genuine native OpenELIS state, but it discards the source
identity and exact replay duplicates that state. No fuzzy-name fallback is safe or proposed.

## Native persistence and replay matrix

The fresh native baseline was `provider=0`, `person=0`, `patient=0`, `sample=0`, `analysis=0`.
The synthetic name was exactly `Pilot Test Clinician` (`given="Pilot Test"`,
`family="Clinician"`).

| Case | HTTP / FHIR result | Authoritative native result |
|---|---|---|
| Read submitted source UUID before create | `404` | zero matching `clinlims.provider.fhir_uuid` rows |
| Create Practitioner A with submitted id and `urn:daher-health:openmrs-provider-uuid` identifier | `201`; response id `484e99f0-…` | one `clinlims.person` (`Pilot Test` / `Clinician`) and one `clinlims.provider` (`fhir_uuid=484e99f0-…`, `external_id=NULL`) |
| Search submitted exact source identifier | `200`, `Bundle.total=0` | source identifier exists nowhere in the provider row |
| Direct read and exact OpenELIS-native `provider_uuid` search | `200`; search `total=1` | same native provider row |
| Replay byte-identical Practitioner A input | `201`; new id `b5e611b6-…` | **second** native provider/person row; unintended duplicate proven |
| Different source UUID, same synthetic name | `201`; new id `1609bd62-…` | third distinct provider/person row, but source identifiers still absent |

The submitted FHIR resource id was not preserved on create. HAPI create processing supplied a new
id before `PractitionerProvider.create`; `transformToProvider` persisted only `active`, that new
FHIR UUID, Person name, and telecom fields. It never reads `Practitioner.identifier`.

## Order requester behavior

A synthetic Patient was created through the patched native Patient path and linked to its OpenMRS
UUID in `clinlims.daher_patient_identity`. A synthetic new order used the already-proven
transport-only LOINC `736-9`; the untouched placeholder accession path generated
`DEV01260000000000001` without configuring or claiming a real site number.

| Requester case | Result | Native requester attribution |
|---|---|---|
| Existing native Practitioner `484e99f0-…` | `201` | `sample_human.provider_id=2`, joined exactly to that provider/person |
| Requester omitted | `201` | order/sample/analysis created; `sample_human.provider_id=NULL` |
| `Practitioner/not-a-uuid` | `500`, invalid UUID | no correlated electronic order/sample/analysis created |
| Well-formed but unknown Practitioner UUID | `201` | order/sample/analysis created; `sample_human.provider_id=NULL`; no Practitioner auto-created |

One native fallback `UNKNOWN_` provider row appeared while the order paths initialized requester
state, but neither missing nor unresolved order linked to it. It does not preserve the source UUID
and is not a safe mapping seam.

The direct ServiceRequest code explains the behavior: it calls
`providerService.getProviderByFhirId(UUID.fromString(requesterId))`; a miss simply leaves the
provider fields empty. It never fetches or creates the referenced Practitioner.

## Split-store / false-success control

The secondary standalone FHIR service was stopped while the business API remained live. Creating
another Practitioner returned `201` and committed native provider/person id `d27a616e-…`.
`GET /Practitioner/d27a616e-…` (native read) returned `200`, while identifier search (forwarded to
the secondary store) returned `500`. After the secondary service restarted, that search returned
`200`, `total=0`; no automatic backfill occurred. `FhirProviderUtils.syncToFhirStore` deliberately
logs and suppresses sync failures, so `201` means native creation succeeded but does not prove the
search store contains it.

This is the inverse of OpenELIS Patient bug 19 but has the same operational consequence: a client
cannot treat either the POST status or the forwarded identifier search as an authoritative
source-identity invariant.

## Existing alternative path is not the current architecture

The pinned source's remote Task-pull workflow can fetch a referenced remote Practitioner and call
`insertOrUpdateProviderByFhirUuid` using its remote resource id. DH-011 uses the already-decided
Option-F adapter push path, not OpenELIS's remote poller. Switching mechanisms would change the
architecture and polling ownership; it was not activated or treated as implicit permission.

## Smallest safe proposal — decision required, not implemented

Within the current push architecture, the smallest crash-safe design is the Practitioner analogue
of DH-011G2's patient identity mechanism:

1. a Daher-owned native link table with unique `(identifier_system, identifier_value)` and a
   foreign key to `clinlims.provider`;
2. persist exactly `urn:daher-health:openmrs-provider-uuid | <OpenMRS Provider UUID>` in the same
   transaction as native provider/person creation;
3. route that exact token search to the native link, with `0 / 1 / ambiguous` fail-closed behavior;
4. adapter probe-first: `1` reuses, `0` creates once, any other result writes nothing; and
5. never match names, telecom, facility id, or OpenELIS-generated provider UUID as a substitute.

An adapter-only mapping table is not sufficient: a crash after OpenELIS commits but before the
adapter stores the returned random UUID recreates the demonstrated duplicate on recovery. The
native atomic link closes that gap. This is new persistence/provider behavior and therefore an
architecture decision. Per the ticket instruction, implementation stops here.

## Status

- **DH-011H privilege increment:** `Add Orders` was applied first; its live proof found the
  technically required `Get Concepts` read and stopped. Liban subsequently approved that exact
  second privilege (ADR-035); the follow-up runtime result is recorded in the append-only project
  state/provenance correction.
- **DH-011H practitioner technical mechanism:** **NOT DONE / DECISION REQUIRED**.
- **Synthetic input name:** available as `Pilot Test Clinician`, but not safely intake-mapped.
- **Real facility input:** waiting on real staff names; none were invented.

---

## DH-011H2 decision correction — 2026-08-31

Liban approved the proposal above as ADR-037. The canonical identity is exactly
`urn:daher-health:openmrs-provider-uuid | <OpenMRS Provider UUID>` in a Daher-owned native mapping;
name/fuzzy fallback and secondary-store dependence remain forbidden. Display-name update policy is
OPEN.

Implementation did not start: the preceding fresh OpenMRS privilege phase exposed an unapproved
fourth read privilege, `Get Providers`, and DH-011H2 required an immediate stop. Therefore the
mechanism is now **DECIDED / NOT IMPLEMENTED**, not decision-pending.
