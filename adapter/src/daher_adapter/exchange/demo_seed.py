"""DH-DEMO-001: the DHealth Native Demo's own reset/seed logic.

Not a fixture generator and not a second data-access layer -- every write below goes through the
exact same repository classes `exchange/api.py` calls for a real request (`OrganizationRepository`,
`CatalogRepository`, `ProviderLabRouteRepository`, `LabDirectoryProfileRepository`,
`LabResultTemplateRepository`, `PatientRepository`, `ExchangeIdentityRepository`,
`OrganizationMembershipRepository`, `OrganizationRoleAssignmentRepository`), so the demo's
synthetic data is subject to the identical validation, uniqueness, and tenant rules any other
caller is. This module's only job is *which* calls to make, in *what* order, and the safety
boundary that gates ever calling them destructively.

Never imported by `app.py` -- this is a standalone CLI (`python -m daher_adapter.exchange.
demo_seed reset`), run only from inside the native demo compose file's own `adapter` container
(`scripts/demo/up.sh`/`reset.sh`), never part of the running FastAPI service's own request path.

RESET SAFETY IS P0 (the ticket's own words): `_require_demo_environment` below checks three
independent facts before anything destructive runs -- the connected database's own name, a
marker row only this exact demo's own init script (`demo/native/db-init/
init-tenant-databases.sh`) ever creates, and an environment variable only the demo compose file
ever sets. All three must hold. A real deployment's adapter can satisfy at most one of them by
coincidence (its own `ADAPTER_DB_NAME` default is literally `adapter`, not
`dhealth_native_demo` -- see `docker-compose.yml` vs. `docker-compose.native-demo.yml`), and can
never satisfy the second or third at all.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.config import Settings
from daher_adapter.database import build_engine, build_session_factory
from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.lab_result_templates import (
    LabResultTemplateRepository,
    TemplateObservationDefinitionInput,
)
from daher_adapter.exchange.membership import (
    ExchangeIdentityRepository,
    OrganizationMembershipRepository,
    OrganizationRoleAssignmentRepository,
)
from daher_adapter.exchange.models import (
    ClinicService,
    ClinicServicePrice,
    FacilityEnrollment,
    LabPrice,
    OnboardingStatus,
    OrganizationMembershipStatus,
    OrganizationRole,
    OrganizationType,
    ParticipationMode,
    Patient,
)
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.patients import PatientRepository
from daher_adapter.exchange.provider_lab_routes import ProviderLabRouteRepository
from daher_adapter.models import utc_now

# -- fixed baseline identity (natural keys, never hardcoded UUIDs -- see module docstring) -------

PROVIDER_ORG_NAME = "Clinique Démo DHealth"
LAB_ORG_NAME = "Laboratoire Démo DHealth"
CATALOG_CODE = "DEMO-TEST-A"
CATALOG_DISPLAY_NAME = "Analyse de démonstration A"
PATIENT_DISPLAY_NAME = "Patient Démo Alpha"
PATIENT_IDEMPOTENCY_KEY = "dhealth-native-demo-patient-alpha"
#: DHEALTH-BILLING-001: the demo patient needs a date of birth, and the reason is a product one.
#:
#: The billing patient lookup returns `display_name` **and** `date_of_birth` precisely because two
#: people sharing a name is ordinary and a till that cannot tell them apart bills the wrong one
#: (ADR-066). A baseline patient with no date of birth makes the demo show the degraded case as if
#: it were the normal one, and the browser qualification's own identification assertion has nothing
#: to find. Synthetic, like every other value in this file.
PATIENT_DATE_OF_BIRTH = date(1990, 4, 17)
#: DHEALTH-BILLING-001: what the demo clinic performs and charges for, in DJF. Ordinary consulting
#: amounts for Djibouti, invented for the demo and carrying no clinical meaning whatsoever -- a
#: price is what a facility charges, never a statement about care.
CLINIC_SERVICES = (
    ("Consultation générale", "3000.00"),
    ("Consultation de suivi", "1500.00"),
)
#: DHEALTH-BILLING-001: what the demo laboratory charges for its one offering, in DJF. Synthetic on
#: the same terms. Without it the laboratory till correctly offers the performed test and correctly
#: refuses to charge for it -- which is right behaviour against a wrong baseline.
LAB_PRICE_AMOUNT = "3500.00"
SEED_IDENTITY_ISSUER = "dhealth-native-demo-seed"
SEED_IDENTITY_SUBJECT = "demo-seed-script"

TEMPLATE_OBSERVATIONS = [
    TemplateObservationDefinitionInput(
        source_code="DEMO-MEASURE-A",
        display_label="Mesure de démonstration A",
        result_type="NUMERIC",
        numeric_unit="u/L",
        required=True,
        sort_order=1,
    ),
    TemplateObservationDefinitionInput(
        source_code="DEMO-MEASURE-B",
        display_label="Mesure de démonstration B",
        result_type="TEXT",
        numeric_unit=None,
        required=True,
        sort_order=2,
    ),
]

# DH-DEMO-001 "DO NOT EXPAND THE DOMAIN": every table below is workflow state a fresh golden
# journey creates (orders, results, releases, amendments, reviews, patient-access grants) --
# never a baseline/configuration table (organizations, catalog, offerings, routes, templates,
# identities, memberships, the seed patient itself). Listed explicitly rather than relying only
# on TRUNCATE's own CASCADE discovery, so a table this list is missing fails loudly (a
# foreign-key error) instead of silently leaving stale rows behind.
#
# `exchange_lab_prices` is included because `CatalogRepository.set_price` is a real, reachable write
# path (the marketplace `select()` path needs it), so a demo operator who exercises it leaves a row
# behind that a "restore the deterministic baseline" reset must clear.
#
# **DHEALTH-BILLING-001 changed what happens next.** `seed_baseline` now *does* create a row here
# (`_ensure_lab_price`), because a laboratory with no price cannot bill the test it performed — the
# till correctly offers it and correctly refuses to charge, which is right behaviour against a wrong
# baseline. `run_reset` truncates and *then* re-seeds, so a price a demo operator changed is still
# cleared and the canonical amount is restored. Same shape as `exchange_clinic_service_prices`.
_WORKFLOW_TABLES = (
    "exchange_lab_prices",
    "exchange_lab_order_intent_items",
    "exchange_lab_order_intents",
    "exchange_release_manifest_entries",
    "exchange_release_versions",
    "exchange_doctor_reviews",
    "exchange_amendments",
    "exchange_result_deliveries",
    "exchange_lab_result_releases",
    "exchange_lab_observations",
    "exchange_lab_results",
    "exchange_lab_sample_collections",
    # DHEALTH-SPECIMEN-001. Physical specimen records are workflow state a laboratory *produces* --
    # nothing seeds one -- so a reset that left them behind would make this module's "restore the
    # deterministic baseline" contract false for specimens specifically, and would leave a freshly
    # reset demo holding collection provenance for orders that no longer exist.
    #
    # Ordered before `exchange_lab_orders` (a `RESTRICT` foreign key to the order) and before
    # `exchange_identities` is ever touched (a `RESTRICT` foreign key to the recording actor).
    # TRUNCATE's own CASCADE would sort the ordering out regardless; the explicit position is what
    # makes a missing entry fail loudly, which is this list's whole reason for existing.
    "exchange_lab_specimens",
    # DHEALTH-LAB-BLOCKED-001. Block episodes are workflow state a laboratory *produces*; nothing
    # seeds one, so a reset that left them behind would make this module's "restore the
    # deterministic baseline" contract false for blocked work specifically -- and would leave a
    # freshly-reset demo with orders claiming to be blocked for reasons whose orders no longer
    # exist.
    #
    # Ordered before BOTH `exchange_lab_selections` and `exchange_lab_orders`, not just before
    # the orders: this table carries a composite `RESTRICT` foreign key to the *selection*
    # (`fk_exchange_lab_order_blocks_selection`) as well as a plain one to the order. TRUNCATE's
    # own CASCADE would sort the ordering out regardless; the explicit position is what makes a
    # missing entry fail loudly, which is this list's whole reason for existing.
    "exchange_lab_order_blocks",
    "exchange_patient_access_grants",
    "exchange_lab_order_items",
    "exchange_lab_selections",
    "exchange_lab_orders",
    # DHEALTH-CLINIC-VISIT-001. A visit is workflow state a clinician creates, not baseline: nothing
    # seeds one, and a reset that left them behind would make this module's "restore the
    # deterministic baseline" contract false for visits specifically. The seed *patient* stays
    # baseline and is deliberately still absent from this list -- the patient is configuration a
    # demo needs to exist; their visits are what a demo produces.
    #
    # Ordered after `exchange_lab_orders` because both orders and intents carry a `RESTRICT`
    # foreign key to `exchange_visits`, and notes before visits for the same reason. TRUNCATE's own
    # CASCADE would sort this out, but the explicit order is what makes a missing table fail loudly,
    # which is this list's whole reason for existing.
    "exchange_visit_notes",
    "exchange_visits",
    # DHEALTH-BILLING-001. Financial artefacts a demo *produces*, cleared for the same reason
    # visits are: nothing seeds an invoice, and a reset that left one behind would make this
    # module's "restore the deterministic baseline" contract false for money specifically.
    #
    # Naming them explicitly is not optional here. `exchange_invoice_lines` references
    # `exchange_lab_order_items`, so TRUNCATE's CASCADE would have emptied the lines while leaving
    # the invoices standing -- every historical invoice silently reduced to a total of zero, with
    # its payments still attached. Stale rows are the ordinary cost of a missing entry in this
    # list; *contradictory* rows are what this one prevents.
    #
    # `exchange_invoice_number_sequences` goes with them so numbering restarts at `FAC-YYYY-000001`
    # rather than resuming above invoices that no longer exist.
    #
    # `exchange_clinic_service_prices` is here and `exchange_clinic_services` is not, which is the
    # same split `exchange_lab_prices` and `exchange_lab_offerings` already use: the service is
    # configuration the demo needs to exist, its price is state a demo operator may change.
    # `_ensure_clinic_service` re-establishes the canonical price on every reset.
    "exchange_payments",
    "exchange_invoice_lines",
    "exchange_invoices",
    "exchange_invoice_number_sequences",
    "exchange_clinic_service_prices",
)

_MARKER_TABLE = "dhealth_demo_environment"
_MARKER_VALUE = "dhealth-native-demo"
_REQUIRED_DB_NAME = "dhealth_native_demo"


class DemoEnvironmentRefused(RuntimeError):
    """Raised by `_require_demo_environment` when any one of the three independent safety facts
    does not hold. Caught only by this module's own CLI `main()`, which prints it to stderr as
    `[demo_seed] REFUSED: ...` and exits 1 -- never treated as success, and never allowed to reach
    `reset_workflow_state`."""


def _require_demo_environment(settings: Settings, session: Session) -> None:
    if settings.db_name != _REQUIRED_DB_NAME:
        raise DemoEnvironmentRefused(
            f"refusing to run against database {settings.db_name!r} -- the DHealth Native Demo "
            f"always connects to {_REQUIRED_DB_NAME!r} (see docker-compose.native-demo.yml)"
        )
    if os.environ.get("ADAPTER_DEMO_ENVIRONMENT", "").strip() != "1":
        raise DemoEnvironmentRefused(
            "refusing to run -- ADAPTER_DEMO_ENVIRONMENT=1 is not set in this process's own "
            "environment (only docker-compose.native-demo.yml's adapter service sets it)"
        )
    try:
        marker = session.execute(
            text(f"SELECT marker FROM {_MARKER_TABLE} WHERE marker = :marker"),  # noqa: S608
            {"marker": _MARKER_VALUE},
        ).scalar_one_or_none()
    except Exception as error:  # noqa: BLE001 -- fail-closed: any error here means "cannot
        # confirm this is a demo database" (most commonly: the table does not exist at all,
        # which is exactly what a real, non-demo deployment looks like), never a crash.
        session.rollback()
        raise DemoEnvironmentRefused(
            f"refusing to run -- could not read the {_MARKER_TABLE!r} marker table ({error}); "
            f"this database was not initialized by demo/native/db-init/init-tenant-databases.sh"
        ) from error
    if marker != _MARKER_VALUE:
        raise DemoEnvironmentRefused(
            f"refusing to run -- {_MARKER_TABLE!r} does not carry the expected marker row; this "
            f"database was not initialized by demo/native/db-init/init-tenant-databases.sh"
        )


class KeycloakLookupError(RuntimeError):
    """Wraps any failure talking to Keycloak's admin API (login, or the user lookup itself)."""


def _keycloak_admin_token(base_url: str, admin_user: str, admin_password: str) -> str:
    request = urllib.request.Request(  # noqa: S310 -- fixed internal-network admin URL, not user input
        f"{base_url}/realms/master/protocol/openid-connect/token",
        data=urllib.parse.urlencode(
            {
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": admin_user,
                "password": admin_password,
            }
        ).encode("ascii"),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
            body = json.loads(response.read())
    except urllib.error.URLError as error:
        raise KeycloakLookupError(f"could not reach Keycloak admin API: {error}") from error
    token = body.get("access_token")
    if not token:
        raise KeycloakLookupError("Keycloak admin login did not return an access_token")
    return token


def discover_keycloak_subject(
    base_url: str, admin_user: str, admin_password: str, username: str
) -> str:
    """Returns the exact `id` Keycloak assigned this username -- read fresh every call, never
    assumed stable across a fresh volume (see module docstring)."""
    token = _keycloak_admin_token(base_url, admin_user, admin_password)
    request = urllib.request.Request(  # noqa: S310
        f"{base_url}/admin/realms/dhealth-exchange/users?username={urllib.parse.quote(username)}&exact=true",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
            users = json.loads(response.read())
    except urllib.error.URLError as error:
        raise KeycloakLookupError(f"could not look up user {username!r}: {error}") from error
    if len(users) != 1:
        raise KeycloakLookupError(
            f"expected exactly one Keycloak user named {username!r}, found {len(users)} -- run "
            f"keycloak/reconcile-demo-users.sh first"
        )
    return users[0]["id"]


def _seed_identity(identities: ExchangeIdentityRepository) -> uuid.UUID:
    record, _created = identities.get_or_create(
        issuer=SEED_IDENTITY_ISSUER, subject=SEED_IDENTITY_SUBJECT, display_name="Demo seed script"
    )
    return record.id


def _demo_user_identity(
    identities: ExchangeIdentityRepository,
    *,
    issuer: str,
    keycloak_base_url: str,
    admin_user: str,
    admin_password: str,
    username: str,
) -> uuid.UUID:
    subject = discover_keycloak_subject(keycloak_base_url, admin_user, admin_password, username)
    record, _created = identities.get_or_create(
        issuer=issuer, subject=subject, display_name=username
    )
    return record.id


def _grant_membership(
    session_factory: sessionmaker[Session],
    memberships: OrganizationMembershipRepository,
    roles: OrganizationRoleAssignmentRepository,
    *,
    organization_id: uuid.UUID,
    identity_id: uuid.UUID,
    org_roles: list[OrganizationRole],
) -> None:
    """Converge one demo membership and its roles toward the declared baseline.

    **The status convergence is not decoration, and it was a real bug.** Before
    DHEALTH-NATIVE-ACCESS-001 this function created the membership and assigned the roles, and
    nothing else. `create` is insert-first-wins, so on a re-seed it returns the *existing* row --
    including an existing row whose status is INACTIVE -- and `assign` then refuses an inactive
    membership with `MembershipInactive`. So a demo database holding one deactivated membership made
    `reset.sh` **crash**, and there was no supported way back to the baseline.

    That was already latently true (a `psql` deactivation could reach it, which is precisely what
    DHEALTH-NATIVE-SEC-001's SEC-6 did), and `docs/NATIVE-SECURITY-QUALIFICATION.md` told an
    operator that `reset.sh` plus re-running the user reconciliation was the recovery -- a claim
    this function could not honour. DHEALTH-NATIVE-ACCESS-001 turns it from latent into ordinary:
    withdrawing access is now a supported product action, so a killed qualification run leaves
    exactly this state behind. Reproduced before fixing.

    Converging the status is squarely inside `seed_baseline`'s own contract rather than an expansion
    of it. DH-DEMO-001's "DO NOT EXPAND THE DOMAIN" governs which *tables* a reset clears --
    workflow state, never baseline configuration -- and this adds nothing to either list. A
    membership the baseline declares ACTIVE, left INACTIVE, simply means the baseline was not
    restored, and restoring it is the whole job. It is additive in the sense that matters: no row is
    deleted, no history is discarded, and the membership keeps its identity and its audit trail.
    """
    membership, _created = memberships.create(
        organization_id=organization_id, identity_id=identity_id
    )
    if membership.status != OrganizationMembershipStatus.ACTIVE.value:
        with session_factory() as session, session.begin():
            memberships.set_status_in_session(
                session,
                membership_id=membership.id,
                status=OrganizationMembershipStatus.ACTIVE,
            )
    for role in org_roles:
        roles.assign(membership_id=membership.id, role=role)


def seed_baseline(session_factory: sessionmaker[Session]) -> None:
    """Idempotent -- every repository call below is insert-first-wins or upsert-by-natural-key
    (see each one's own docstring), so running this against an already-seeded database is a safe
    no-op, never a duplicate. Called by both `up.sh` (first boot) and `reset.sh` (every run)."""
    organizations = OrganizationRepository(session_factory)
    catalog = CatalogRepository(session_factory, organizations)
    directory = LabDirectoryProfileRepository(session_factory, organizations)
    routes = ProviderLabRouteRepository(session_factory, organizations)
    templates = LabResultTemplateRepository(session_factory)
    patients = PatientRepository(session_factory, organizations)
    identities = ExchangeIdentityRepository(session_factory)
    memberships = OrganizationMembershipRepository(session_factory)
    roles = OrganizationRoleAssignmentRepository(session_factory)

    seed_identity_id = _seed_identity(identities)

    provider = _find_or_create_organization(
        organizations, org_type="PROVIDER", name=PROVIDER_ORG_NAME
    )
    lab = _find_or_create_organization(organizations, org_type="LAB", name=LAB_ORG_NAME)

    directory.upsert(
        lab_organization_id=lab.id,
        public_address="Adresse de démonstration, Djibouti",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=False,
        accepting_orders=True,
    )
    routes.upsert(provider_organization_id=provider.id, lab_organization_id=lab.id, active=True)

    # DHEALTH-NATIVE-MVP-1.0 (2026-09-25): the facility enrollment DH-MVP-ONBOARD-001 requires,
    # which this seed never created.
    #
    # `find_whole_order_eligible_labs` narrows by route and offering and then asks
    # `operational_findings_in_session` whether the laboratory may receive new work. That function
    # fails closed on a missing `FacilityEnrollment` -- "cet établissement n'a pas de dossier
    # d'adhésion" -- and there was no such row for either demo organization, so the laboratory was
    # never an eligible destination, the routing step offered an empty list, and **the demo's
    # golden journey could not be completed by anyone**. Found by driving it in a real browser: the
    # order was created, the routing panel opened, and there was nothing in it to choose.
    #
    # NATIVE for both, because that is what this demo is: DHealth's own screens are the workflow,
    # no connector exists, and `READY_FOR_ROUTING` is reachable only by a NATIVE facility in this
    # release. Converged like everything else here rather than inserted blindly.
    _ensure_enrollment(session_factory, provider.id)
    _ensure_enrollment(session_factory, lab.id)

    # DHEALTH-BILLING-001: what the demo clinic charges for. Two services rather than one, so the
    # price list is visibly a list and a clerk's first action is a real choice.
    for service_name, service_amount in CLINIC_SERVICES:
        _ensure_clinic_service(
            session_factory, provider.id, name=service_name, amount=service_amount
        )

    entry, _created = catalog.create_entry(code=CATALOG_CODE, display_name=CATALOG_DISPLAY_NAME)
    offering, _created = catalog.create_offering(
        lab_organization_id=lab.id, catalog_entry_id=entry.id
    )

    existing_templates = templates.list_templates_for_offering(offering.id)
    if existing_templates:
        template = existing_templates[0]
    else:
        template, _created = templates.create_template(
            lab_offering_id=offering.id, version=1, observations=TEMPLATE_OBSERVATIONS
        )
    active = templates.get_active_templates_for_offerings([offering.id])
    current_active_id = active[offering.id].id if offering.id in active else None
    if current_active_id != template.id:
        templates.activate_template(
            lab_offering_id=offering.id,
            template_id=template.id,
            expected_current_template_id=current_active_id,
        )

    # DHEALTH-BILLING-001: the laboratory's own price for the test it performs.
    #
    # Nothing seeded this before, and the consequence was only visible once a laboratory clerk tried
    # to use the till: `Facturation` correctly offered the performed test and correctly refused to
    # charge for it — "Tarif non défini", control disabled — because `_lab_item_price` reads
    # `LabPrice` through `LabOffering` and there was none. That is the right behaviour (a zero line
    # would silently claim the test is free) and the wrong baseline: a demo laboratory that cannot
    # bill its own work cannot show the workflow this ticket added.
    #
    # `exchange_lab_prices` stays in `_WORKFLOW_TABLES`. `run_reset` truncates and *then* calls this
    # function, so a demo operator's own price change is still cleared and the canonical amount is
    # re-established — the same shape as `exchange_clinic_service_prices`.
    _ensure_lab_price(session_factory, catalog, offering.id, amount=LAB_PRICE_AMOUNT)

    patients.create(
        provider_organization_id=provider.id,
        idempotency_key=PATIENT_IDEMPOTENCY_KEY,
        display_name=PATIENT_DISPLAY_NAME,
        # Deliberately `None` here, with the date set by `_ensure_patient_date_of_birth` below.
        #
        # `PatientRepository.create` is idempotent on its key *and* refuses a reuse whose content
        # differs -- which is right, and which means passing the date here makes every re-seed of a
        # database created before this ticket raise `PatientRegistrationConflict` and abort the
        # whole reset. Found by running it. One convergent path, so a fresh database and an existing
        # one end up identical.
        date_of_birth=None,
        contact_channel_type=None,
        contact_value=None,
        created_by_identity_id=seed_identity_id,
    )
    _ensure_patient_date_of_birth(session_factory, provider.id)

    keycloak_base_url = os.environ.get("DEMO_KEYCLOAK_ADMIN_URL", "").strip()
    admin_user = os.environ.get("DEMO_KEYCLOAK_ADMIN_USER", "").strip()
    admin_password = os.environ.get("DEMO_KEYCLOAK_ADMIN_PASSWORD", "").strip()
    issuer = os.environ.get("EXCHANGE_OIDC_ISSUER", "").strip()
    keycloak_vars = (keycloak_base_url, admin_user, admin_password, issuer)
    if any(keycloak_vars) and not all(keycloak_vars):
        # Fail loudly, never warn-and-continue: a *partial* set of these four is a real
        # misconfiguration (e.g. a future compose edit dropping one of them), never the
        # intentional unit-test bypass below, which requires all four to be absent. Silently
        # skipping membership grants here would boot a demo whose three logins work but hold no
        # role anywhere -- a green `up.sh` with an unusable golden journey.
        raise RuntimeError(
            "demo_seed: DEMO_KEYCLOAK_ADMIN_URL/DEMO_KEYCLOAK_ADMIN_USER/"
            "DEMO_KEYCLOAK_ADMIN_PASSWORD/EXCHANGE_OIDC_ISSUER must be either all set or all "
            "absent -- got a partial set, which is never intentional"
        )
    if all(keycloak_vars):
        clinician_identity = _demo_user_identity(
            identities,
            issuer=issuer,
            keycloak_base_url=keycloak_base_url,
            admin_user=admin_user,
            admin_password=admin_password,
            username=os.environ.get("DEMO_CLINICIAN_USERNAME", "demo-clinician"),
        )
        _grant_membership(
            session_factory,
            memberships,
            roles,
            organization_id=provider.id,
            identity_id=clinician_identity,
            org_roles=[OrganizationRole.CLINICIAN],
        )

        lab_tech_identity = _demo_user_identity(
            identities,
            issuer=issuer,
            keycloak_base_url=keycloak_base_url,
            admin_user=admin_user,
            admin_password=admin_password,
            username=os.environ.get("DEMO_LAB_TECH_USERNAME", "demo-lab-tech"),
        )
        _grant_membership(
            session_factory,
            memberships,
            roles,
            organization_id=lab.id,
            identity_id=lab_tech_identity,
            org_roles=[OrganizationRole.LAB_TECH],
        )

        releaser_identity = _demo_user_identity(
            identities,
            issuer=issuer,
            keycloak_base_url=keycloak_base_url,
            admin_user=admin_user,
            admin_password=admin_password,
            username=os.environ.get("DEMO_RELEASER_USERNAME", "demo-releaser"),
        )
        _grant_membership(
            session_factory,
            memberships,
            roles,
            organization_id=lab.id,
            identity_id=releaser_identity,
            org_roles=[OrganizationRole.LAB_RESULT_RELEASER],
        )

        # DHEALTH-NATIVE-ACCESS-001: one facility administrator per organization.
        #
        # Two administrators rather than one shared between both organizations, deliberately. The
        # cross-organization boundary is only a real question if the caller genuinely *is* an
        # administrator somewhere else -- an identity holding ORG_ADMIN in both would legitimately
        # succeed everywhere and would prove nothing about tenant isolation.
        #
        # ORG_ADMIN only, and no clinical role. That is the honest shape of a facility
        # administrator (ADR-044 and `require_clinician` both refuse an ORG_ADMIN who holds only
        # that), and it is also the configuration that was unreachable before this ticket: such a
        # membership was not "usable" in the staff application, so its holder could not open a
        # single screen. Seeding it clinical roles as well would quietly hide whether the new
        # administration surface is reachable on its own.
        admin_identity = _demo_user_identity(
            identities,
            issuer=issuer,
            keycloak_base_url=keycloak_base_url,
            admin_user=admin_user,
            admin_password=admin_password,
            username=os.environ.get("DEMO_ADMIN_USERNAME", "demo-admin"),
        )
        _grant_membership(
            session_factory,
            memberships,
            roles,
            organization_id=provider.id,
            identity_id=admin_identity,
            org_roles=[OrganizationRole.ORG_ADMIN],
        )

        lab_admin_identity = _demo_user_identity(
            identities,
            issuer=issuer,
            keycloak_base_url=keycloak_base_url,
            admin_user=admin_user,
            admin_password=admin_password,
            username=os.environ.get("DEMO_LAB_ADMIN_USERNAME", "demo-lab-admin"),
        )
        _grant_membership(
            session_factory,
            memberships,
            roles,
            organization_id=lab.id,
            identity_id=lab_admin_identity,
            org_roles=[OrganizationRole.ORG_ADMIN],
        )

        # DHEALTH-BILLING-001/ADR-066: one cashier per organization, and a cashier is nothing else.
        #
        # `BILLING_CLERK` only -- no clinical role, and deliberately *not* also ORG_ADMIN. ADR-066
        # records that being an administrator, a clinician or a lab worker must not automatically
        # make someone a cashier, and a baseline whose cashier were also the administrator could
        # not demonstrate it. This shape is what makes both directions checkable in a real browser:
        # the cashier opens `Facturation` and no clinical screen, and the clinician opens the
        # clinical screens and no till.
        #
        # Two, one per organization, for the same reason there are two administrators: a single
        # identity holding the role in both would legitimately succeed everywhere and would prove
        # nothing about the tenant boundary.
        clerk_identity = _demo_user_identity(
            identities,
            issuer=issuer,
            keycloak_base_url=keycloak_base_url,
            admin_user=admin_user,
            admin_password=admin_password,
            username=os.environ.get("DEMO_CLERK_USERNAME", "demo-clerk"),
        )
        _grant_membership(
            session_factory,
            memberships,
            roles,
            organization_id=provider.id,
            identity_id=clerk_identity,
            org_roles=[OrganizationRole.BILLING_CLERK],
        )

        lab_clerk_identity = _demo_user_identity(
            identities,
            issuer=issuer,
            keycloak_base_url=keycloak_base_url,
            admin_user=admin_user,
            admin_password=admin_password,
            username=os.environ.get("DEMO_LAB_CLERK_USERNAME", "demo-lab-clerk"),
        )
        _grant_membership(
            session_factory,
            memberships,
            roles,
            organization_id=lab.id,
            identity_id=lab_clerk_identity,
            org_roles=[OrganizationRole.BILLING_CLERK],
        )
    else:
        print(  # noqa: T201 -- CLI tool, stdout is the intended channel
            "[demo_seed] skipping demo-user membership grants: Keycloak admin lookup not "
            "configured in this environment (expected only when unit-testing seed_baseline "
            "in isolation, never when run via scripts/demo/up.sh or reset.sh)",
            file=sys.stderr,
        )


def _ensure_enrollment(session_factory, organization_id) -> None:
    """One NATIVE, routing-ready enrollment per demo organization. Idempotent.

    Written directly against the model rather than through the platform-admin onboarding API
    because this seed has no HTTP client and no platform-admin token, and because the demo's
    baseline is declared, not walked through a wizard. The row it writes is the same shape that
    wizard produces for a NATIVE facility that has completed onboarding.
    """
    with session_factory() as session, session.begin():
        existing = session.scalar(
            select(FacilityEnrollment).where(FacilityEnrollment.organization_id == organization_id)
        )
        if existing is None:
            session.add(
                FacilityEnrollment(
                    organization_id=organization_id,
                    participation_mode=ParticipationMode.NATIVE.value,
                    onboarding_status=OnboardingStatus.READY_FOR_ROUTING.value,
                )
            )
            return
        existing.participation_mode = ParticipationMode.NATIVE.value
        existing.onboarding_status = OnboardingStatus.READY_FOR_ROUTING.value


def _ensure_lab_price(session_factory, catalog: CatalogRepository, offering_id, *, amount) -> None:
    """The demo laboratory's current price for its own offering. Idempotent.

    DHEALTH-BILLING-001. `CatalogRepository.set_price` is an optimistic-concurrency update: it wants
    to be told which price row the caller believes is current, and returns `"unchanged"` when the
    amount already matches. So the current row is read first and passed as the expectation, which
    makes a second run a no-op rather than a `PriceConflict`.
    """
    with session_factory() as session:
        current = session.scalar(
            select(LabPrice).where(
                LabPrice.lab_offering_id == offering_id, LabPrice.superseded_at.is_(None)
            )
        )
        current_id = current.id if current is not None else None
        already = current is not None and current.amount == Decimal(amount)
    if already:
        return
    catalog.set_price(
        offering_id=offering_id,
        amount=Decimal(amount),
        currency="DJF",
        expected_current_price_id=current_id,
    )


def _ensure_patient_date_of_birth(session_factory, provider_organization_id) -> None:
    """Fill the baseline patient's date of birth when it is missing. Idempotent.

    DHEALTH-BILLING-001. `PatientRepository.create` is idempotent on its key, so a database seeded
    before this ticket keeps the `NULL` it was created with and no amount of re-seeding changes it —
    which would leave the demo's only patient unable to demonstrate the identification the billing
    lookup exists to provide.

    Deliberately **only when absent**: this converges a missing baseline fact and never overwrites a
    date somebody entered. Nothing else about the patient is touched.
    """
    with session_factory() as session, session.begin():
        patient = session.scalar(
            select(Patient).where(
                Patient.provider_organization_id == provider_organization_id,
                Patient.display_name == PATIENT_DISPLAY_NAME,
            )
        )
        if patient is not None and patient.date_of_birth is None:
            patient.date_of_birth = PATIENT_DATE_OF_BIRTH


def _ensure_clinic_service(session_factory, organization_id, *, name: str, amount: str) -> None:
    """One priced service the demo clinic actually performs and charges for. Idempotent.

    DHEALTH-BILLING-001. Written directly against the models rather than through
    `BillingRepository.create_service`, which needs a principal and refuses a duplicate name by
    design -- the seed has neither a principal nor any business raising `ServiceNameTaken` on its
    second run.

    The *service* is baseline configuration, exactly like `LabOffering`: without one the till has
    nothing to sell and `Facturation` opens onto an empty catalogue. Its *price*, like `LabPrice`,
    is workflow state a demo may change, so `_WORKFLOW_TABLES` truncates
    `exchange_clinic_service_prices` and this function re-establishes the canonical amount --
    which is why the price is reconciled below rather than only created.
    """
    with session_factory() as session, session.begin():
        service = session.scalar(
            select(ClinicService).where(
                ClinicService.organization_id == organization_id, ClinicService.name == name
            )
        )
        if service is None:
            service = ClinicService(organization_id=organization_id, name=name, active=True)
            session.add(service)
            session.flush()
        current = session.scalar(
            select(ClinicServicePrice).where(
                ClinicServicePrice.clinic_service_id == service.id,
                ClinicServicePrice.superseded_at.is_(None),
            )
        )
        if current is not None and current.amount == Decimal(amount):
            return
        if current is not None:
            # Supersede rather than overwrite -- the same immutable-price discipline
            # `BillingRepository.change_price` follows, so a demo reset leaves a truthful price
            # history rather than a rewritten one.
            current.superseded_at = utc_now()
        session.add(
            ClinicServicePrice(clinic_service_id=service.id, amount=Decimal(amount), currency="DJF")
        )


def _find_or_create_organization(
    organizations: OrganizationRepository, *, org_type: str, name: str
):
    for existing in organizations.list_active(OrganizationType(org_type)):
        if existing.name == name:
            return existing
    return organizations.create(org_type=org_type, name=name)


def reset_workflow_state(session: Session) -> None:
    """`TRUNCATE ... CASCADE`, never a row-by-row `DELETE` -- every Exchange foreign key in this
    schema is deliberately `ON DELETE RESTRICT` (immutable-history discipline the domain itself
    enforces, see `exchange/models.py`), which a per-row DELETE would hit immediately on any
    order with a single item. `TRUNCATE ... CASCADE` follows the schema's foreign-key graph
    structurally instead of per-row delete triggers, so it empties every listed table (plus
    anything referencing them this list does not already name) without touching a single
    baseline/configuration row -- organizations, catalog, offerings, routes, templates,
    identities, memberships, and the seed patient are never named here and therefore never
    truncated."""
    table_list = ", ".join(_WORKFLOW_TABLES)
    session.execute(text(f"TRUNCATE TABLE {table_list} CASCADE"))  # noqa: S608


def run_reset(settings: Settings, session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        _require_demo_environment(settings, session)
        reset_workflow_state(session)
        session.commit()
    seed_baseline(session_factory)
    print("[demo_seed] reset complete: baseline reconciled, workflow state cleared")  # noqa: T201


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="demo_seed")
    parser.add_argument("action", choices=["reset"])
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    engine = build_engine(settings)
    session_factory = build_session_factory(engine)

    try:
        if args.action == "reset":
            run_reset(settings, session_factory)
    except DemoEnvironmentRefused as error:
        print(f"[demo_seed] REFUSED: {error}", file=sys.stderr)  # noqa: T201
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
