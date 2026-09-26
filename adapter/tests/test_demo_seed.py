"""DH-DEMO-001: unit coverage for `demo_seed`'s safety boundary and idempotent baseline seeding.

`reset_workflow_state` (the actual `TRUNCATE ... CASCADE`) is deliberately NOT exercised here --
SQLite does not support `TRUNCATE` and does not enforce foreign keys by default, so any result
against it would prove nothing about the real behavior. That destructive path is qualified only
against real PostgreSQL: see `tests/integration/test_demo_seed_postgres.py`.

Every test below runs with Keycloak admin env vars deliberately unset, exercising
`seed_baseline`'s own documented fallback (skip membership grants, warn, continue) -- the
Keycloak-dependent path (`discover_keycloak_subject`, membership/role grants) is also qualified
only against the PostgreSQL integration test, which is the only place a demo Keycloak realm is
ever actually reachable.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.config import Settings
from daher_adapter.exchange import demo_seed
from daher_adapter.exchange.membership import (
    ExchangeIdentityRepository,
    OrganizationMembershipRepository,
    OrganizationRoleAssignmentRepository,
)
from daher_adapter.exchange.models import (
    LabDirectoryProfile,
    LabOffering,
    Organization,
    OrganizationRole,
    OrganizationType,
)
from daher_adapter.exchange.organizations import OrganizationRepository


def _settings(db_name: str = "dhealth_native_demo") -> Settings:
    return Settings(
        timezone="Africa/Djibouti",
        db_host="unused",
        db_port=5432,
        db_name=db_name,
        db_user="unused",
        db_password="unused",
        audit_path=Path("/tmp/unused.jsonl"),
        retry_max_attempts=1,
        retry_multiplier_seconds=1.0,
        retry_max_seconds=1.0,
    )


@pytest.fixture(autouse=True)
def _no_keycloak_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "DEMO_KEYCLOAK_ADMIN_URL",
        "DEMO_KEYCLOAK_ADMIN_USER",
        "DEMO_KEYCLOAK_ADMIN_PASSWORD",
        "EXCHANGE_OIDC_ISSUER",
    ):
        monkeypatch.delenv(name, raising=False)


def _create_marker(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE dhealth_demo_environment (marker text PRIMARY KEY)"))
        connection.execute(
            text("INSERT INTO dhealth_demo_environment (marker) VALUES ('dhealth-native-demo')")
        )


# -- RS1-RS5: `_require_demo_environment`'s three independent facts -----------------------------


def test_rs1_refuses_wrong_database_name(sessions: sessionmaker[Session], monkeypatch) -> None:
    monkeypatch.setenv("ADAPTER_DEMO_ENVIRONMENT", "1")
    with sessions() as session:
        with pytest.raises(demo_seed.DemoEnvironmentRefused, match="dhealth_native_demo"):
            demo_seed._require_demo_environment(_settings(db_name="adapter"), session)


def test_rs2_refuses_missing_env_marker(sessions: sessionmaker[Session], monkeypatch) -> None:
    monkeypatch.delenv("ADAPTER_DEMO_ENVIRONMENT", raising=False)
    with sessions() as session:
        with pytest.raises(demo_seed.DemoEnvironmentRefused, match="ADAPTER_DEMO_ENVIRONMENT"):
            demo_seed._require_demo_environment(_settings(), session)


def test_rs3_refuses_missing_marker_table_row(
    engine: Engine, sessions: sessionmaker[Session], monkeypatch
) -> None:
    monkeypatch.setenv("ADAPTER_DEMO_ENVIRONMENT", "1")
    with sessions() as session:
        with pytest.raises(demo_seed.DemoEnvironmentRefused, match="dhealth_demo_environment"):
            demo_seed._require_demo_environment(_settings(), session)


def test_rs4_succeeds_when_all_three_facts_hold(
    engine: Engine, sessions: sessionmaker[Session], monkeypatch
) -> None:
    monkeypatch.setenv("ADAPTER_DEMO_ENVIRONMENT", "1")
    _create_marker(engine)
    with sessions() as session:
        demo_seed._require_demo_environment(_settings(), session)  # must not raise


def test_rs5_env_var_value_must_be_exactly_1(
    engine: Engine, sessions: sessionmaker[Session], monkeypatch
) -> None:
    _create_marker(engine)
    monkeypatch.setenv("ADAPTER_DEMO_ENVIRONMENT", "true")
    with sessions() as session:
        with pytest.raises(demo_seed.DemoEnvironmentRefused, match="ADAPTER_DEMO_ENVIRONMENT"):
            demo_seed._require_demo_environment(_settings(), session)


# -- BS1-BS4: `seed_baseline` idempotency and content --------------------------------------------


def test_bs1_seed_baseline_creates_expected_organizations(sessions: sessionmaker[Session]) -> None:
    demo_seed.seed_baseline(sessions)
    with sessions() as session:
        names = {org.name for org in session.query(Organization).all()}
    assert demo_seed.PROVIDER_ORG_NAME in names
    assert demo_seed.LAB_ORG_NAME in names


def test_bs2_seed_baseline_creates_offering_and_active_template(
    sessions: sessionmaker[Session],
) -> None:
    demo_seed.seed_baseline(sessions)
    with sessions() as session:
        offering = session.query(LabOffering).one()
        assert offering.active is True


def test_bs3_seed_baseline_creates_unlisted_accepting_directory_profile(
    sessions: sessionmaker[Session],
) -> None:
    demo_seed.seed_baseline(sessions)
    with sessions() as session:
        profile = session.query(LabDirectoryProfile).one()
        assert profile.listed is False
        assert profile.accepting_orders is True


def test_bs4_seed_baseline_is_idempotent(sessions: sessionmaker[Session]) -> None:
    demo_seed.seed_baseline(sessions)
    demo_seed.seed_baseline(sessions)
    demo_seed.seed_baseline(sessions)
    with sessions() as session:
        organizations = session.query(Organization).all()
        offerings = session.query(LabOffering).all()
        profiles = session.query(LabDirectoryProfile).all()
    assert len(organizations) == 2, "repeat seeding must never create a duplicate organization"
    assert len(offerings) == 1, "repeat seeding must never create a duplicate offering"
    assert len(profiles) == 1, "repeat seeding must never create a duplicate directory profile"


# -- DHEALTH-NATIVE-ACCESS-001: the reset path must survive a withdrawn membership ---------------


def test_bs5_reseeding_restores_a_membership_that_was_deactivated(
    sessions: sessionmaker[Session],
) -> None:
    """`reset.sh` must be able to restore the baseline after a membership was deactivated.

    This was a real bug, reproduced before it was fixed. `_grant_membership` called
    `memberships.create` (insert-first-wins, so a re-seed returns the *existing* row, INACTIVE
    included) and then `roles.assign`, which refuses an inactive membership with
    `MembershipInactive`. So one deactivated membership made `seed_baseline` -- and therefore
    `reset.sh` -- **raise**, with no supported way back to the baseline.

    It was already latently reachable: DHEALTH-NATIVE-SEC-001's SEC-6 produced exactly this state
    with direct SQL, and `docs/NATIVE-SECURITY-QUALIFICATION.md` told an operator that `reset.sh`
    plus re-running the user reconciliation was the recovery -- a claim the code could not honour.
    DHEALTH-NATIVE-ACCESS-001 makes it ordinary rather than latent, because withdrawing access is
    now a supported product action, so a qualification run killed between a deactivation and its
    cleanup leaves precisely this behind.

    Driven through `_grant_membership` rather than `seed_baseline`, because `seed_baseline` skips
    every membership grant when the Keycloak admin lookup is unconfigured (which is the case in the
    unit suite, by its own documented design).
    """
    organizations = OrganizationRepository(sessions)
    identities = ExchangeIdentityRepository(sessions)
    memberships = OrganizationMembershipRepository(sessions)
    roles = OrganizationRoleAssignmentRepository(sessions)

    organization = organizations.create(
        org_type=OrganizationType.PROVIDER.value, name=demo_seed.PROVIDER_ORG_NAME
    )
    identity, _ = identities.get_or_create(
        issuer="dhealth-native-demo-test", subject="demo-clinician", display_name="demo-clinician"
    )

    demo_seed._grant_membership(  # noqa: SLF001 -- the unit under test
        sessions,
        memberships,
        roles,
        organization_id=organization.id,
        identity_id=identity.id,
        org_roles=[OrganizationRole.CLINICIAN],
    )
    seeded = memberships.find_active(organization_id=organization.id, identity_id=identity.id)
    assert seeded is not None

    # What a killed qualification run, or SEC-6, leaves behind.
    memberships.set_inactive(seeded.id)
    assert memberships.find_active(organization_id=organization.id, identity_id=identity.id) is None

    # The re-seed must converge it, not raise.
    demo_seed._grant_membership(  # noqa: SLF001
        sessions,
        memberships,
        roles,
        organization_id=organization.id,
        identity_id=identity.id,
        org_roles=[OrganizationRole.CLINICIAN],
    )

    restored = memberships.find_active(organization_id=organization.id, identity_id=identity.id)
    assert restored is not None, "the baseline was not restored"
    # The same row, never a second membership: identity continuity and audit history survive.
    assert restored.id == seeded.id
    assert roles.list_for_membership(restored.id) == frozenset({OrganizationRole.CLINICIAN.value})
