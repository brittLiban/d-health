# DH-011H2 — pinned OpenMRS Test Order CREATE privilege audit

**Date:** 2026-08-31  
**Scope:** the exact synthetic `POST /ws/rest/v1/order` shape already used by DH-011H1/H2  
**Result:** **STOP — unapproved privileges remain on the exact CREATE path**

## Pinned source

The deployed backend image is based on the digest-pinned OpenMRS O3 `3.7.x` backend in
`openmrs-config/Dockerfile`. Its installed artifacts identify OpenMRS core `2.8.7` and
`webservices.rest` `3.5.0`. The audit used the corresponding exact upstream tags and commits:

- `openmrs-core` tag `2.8.7`: `7ddf30f4fa772e4efd0b201b3bd8a34546247b0a`
- `openmrs-module-webservices.rest` tag `3.5.0`:
  `69fa31fc157be0b0835e2101a0b0e480e0da4acb`

The supported request is `type=testorder` with existing synthetic `patient`, `encounter`,
`concept`, `careSetting`, `orderer`, and `orderType` UUID references. It deliberately supplies no
client-generated `orderNumber` and no `specimenSource`.

## Exact path

`DelegatingCrudResource.create()` converts the request and then calls `save()`. For OpenMRS 2.8.7,
`OrderResource2_2` inherits the CREATE and save behavior from `OrderResource1_10`; the
`TestOrderSubclassHandler1_10` creates a `TestOrder` and reuses those base creatable properties.
`BaseDelegatingResource.setConvertedProperties()` applies supplied properties in the resource
description's insertion order. For this payload, the protected UUID resolutions therefore occur
in this order: encounter, patient, concept, care setting, orderer, then order type.

| Stage | Privilege checked | Already approved? | Read/write/admin | Source file/method |
|---|---|---|---|---|
| Resolve `encounter` UUID | `Get Encounters` | Yes — original admission set | Read | `EncounterResource1_8.getByUniqueId` → `EncounterService.getEncounterByUuid` |
| Resolve `patient` UUID | `Get Patients` | Yes — original admission set | Read | `PatientResource1_8.getByUniqueId` → `PatientService.getPatientByUuid` |
| Resolve `concept` UUID | `Get Concepts` | Yes — ADR-035 | Read | `ConceptResource1_8.getByUniqueId` → `ConceptService.getConceptByUuid` |
| Resolve `careSetting` UUID | `Get Care Settings` | Yes — ADR-036 | Read | `CareSettingResource1_10.getByUniqueId` → `OrderService.getCareSettingByUuid` |
| Resolve `orderer` Provider UUID | `Get Providers` | **Approved by Liban in this continuation, but not applied because this audit reached the stop condition** | Read | `ProviderResource1_9.getByUniqueId` → `ProviderService.getProviderByUuid` |
| Resolve required `orderType` UUID | `Get Order Types` | **No** | Read | `OrderTypeResource1_10.getByUniqueId` → `OrderService.getOrderTypeByUuid` |
| Enter order save | `Edit Orders` **OR** `Add Orders` | `Add Orders` yes — ADR-034; `Edit Orders` is not required | Write | `OrderResource1_10.save` → `OrderService.saveOrder`; `@Authorized` defaults `requireAll=false`, as enforced by `AuthorizationAdvice` |
| Validate the exact `TestOrder` | No additional check for this payload | N/A | Validation only | `RequiredDataAdvice.before` → `ValidateUtil.validate` → `TestOrderValidator` / `ServiceOrderValidator` / `OrderValidator`; the only service call here is conditional on non-null `specimenSource`, which this payload omits |
| Check active orders during save | No intercepted privilege check | N/A | Internal read | `OrderServiceImpl.saveOrder` invokes `getActiveOrders` on the same implementation instance; the public method's `Get Orders` annotation is not crossed through the service proxy on this self-invocation |
| Select order-number generator when its static cache is empty | `Get Global Properties` | **No** | Read (configuration) | `OrderServiceImpl.saveOrderInternal` → `getOrderNumberGenerator` → `AdministrationService.getGlobalProperty` |
| Allocate the native order-number seed using the default generator | `Add Orders` | Yes — ADR-034 | Write | `OrderServiceImpl.getNewOrderNumber` → proxied `OrderService.getNextOrderNumberSeedSequenceValue` |
| Persist native row | No additional privilege check | N/A | Internal write | `OrderServiceImpl.saveOrderInternal` → `OrderDAO.saveOrder` |

The response representation reads the already-resolved delegate and adds no further service
authorization check for the default response.

## Stop result

`Get Order Types` is the deterministic **next** missing privilege. The required `orderType` field
is converted after `orderer` and before `OrderResource1_10.save()`, and
`OrderService.getOrderTypeByUuid()` is annotated with `GET_ORDER_TYPES`.

The audit also proves a later cold-path requirement: because this supported request does not let
the client assign an order number, the first/cache-miss save calls
`AdministrationService.getGlobalProperty(order.orderNumberGeneratorBeanId)`, whose actual 2.8.7
service annotation requires `Get Global Properties`. A warmed static generator cache can hide this
check, so a warm-only runtime success would not establish a reliable minimum privilege set.

Neither privilege was granted. The already-approved `Get Providers` was also left unapplied because
the ticket required the source audit to stop before any role modification if it found another
unapproved privilege. No runtime stack or practitioner implementation followed this finding.

## DH-011H2A correction — effective baseline privilege and response conversion

The source call chain above is correct, but two conclusions from the source-only pass required
runtime qualification. First, OpenMRS's built-in `Authenticated` role already has
`Get Global Properties`, and `UserContext.hasPrivilege()` considers that role for every logged-in
user even when it is not assigned in `user_role`. The ordinary Daher-derived runtime therefore
passes the cold generator lookup: absence from `Daher Clinician Admission` does **not** mean the
clinician lacks the privilege effectively. A true disposable negative control removed it from
`Authenticated`, restarted, and then reproduced the exact 403/zero-row cold failure described by
the source chain.

Second, the final sentence above stating that response representation adds no authorization check
is incorrect. `ConversionUtil.convertToRepresentation()` checks
`OrderResource1_8.getRequiredGetPrivilege()` (`Get Orders`) after
`DelegatingCrudResource.create()` has saved the order. The no-`Get Orders` clinician therefore
commits a warm order and then receives HTTP 500 from a `PRIVILEGE_DENIED` sentinel cast to
`SimpleObject`. `Get Orders` is unapproved and was not granted. Full source, runtime, option, and
configuration-scope evidence is in `docs/DH-011H2A-ORDER-NUMBER-BOUNDARY.md`.
