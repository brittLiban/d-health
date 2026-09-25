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
    FacilityEnrollment,
    OnboardingStatus,
    OrganizationRole,
    OrganizationType,
    ParticipationMode,
)
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.patients import PatientRepository
from daher_adapter.exchange.provider_lab_routes import ProviderLabRouteRepository

# -- fixed baseline identity (natural keys, never hardcoded UUIDs -- see module docstring) -------

PROVIDER_ORG_NAME = "Clinique Démo DHealth"
LAB_ORG_NAME = "Laboratoire Démo DHealth"
CATALOG_CODE = "DEMO-TEST-A"
CATALOG_DISPLAY_NAME = "Analyse de démonstration A"
PATIENT_DISPLAY_NAME = "Patient Démo Alpha"
PATIENT_IDEMPOTENCY_KEY = "dhealth-native-demo-patient-alpha"
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
# `exchange_lab_prices` is included even though `seed_baseline` never creates a row there and the
# ticket's own golden journey (`assign_by_provider`) never touches it: `CatalogRepository.set_price`
# is a real, reachable write path (the marketplace `select()` path needs it), so a demo operator
# who exercises that path leaves a row behind that a "restore the deterministic baseline" reset
# must also clear. It is not baseline/configuration data -- nothing seeds it, so no reset would
# ever need to *restore* a price -- only clear one that live use created.
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
    "exchange_patient_access_grants",
    "exchange_lab_order_items",
    "exchange_lab_selections",
    "exchange_lab_orders",
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
    memberships: OrganizationMembershipRepository,
    roles: OrganizationRoleAssignmentRepository,
    *,
    organization_id: uuid.UUID,
    identity_id: uuid.UUID,
    org_roles: list[OrganizationRole],
) -> None:
    membership, _created = memberships.create(
        organization_id=organization_id, identity_id=identity_id
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

    patients.create(
        provider_organization_id=provider.id,
        idempotency_key=PATIENT_IDEMPOTENCY_KEY,
        display_name=PATIENT_DISPLAY_NAME,
        date_of_birth=None,
        contact_channel_type=None,
        contact_value=None,
        created_by_identity_id=seed_identity_id,
    )

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
            memberships,
            roles,
            organization_id=lab.id,
            identity_id=releaser_identity,
            org_roles=[OrganizationRole.LAB_RESULT_RELEASER],
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
