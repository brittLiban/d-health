#!/usr/bin/env python3
"""DH-IDENTITY-001: real-PostgreSQL migration qualification for 0021 -> 0022
(``exchange_patient_directory``).

Proves, against a real disposable PostgreSQL database (never against SQLite -- Alembic in this
repository only ever targets ``Settings.database_url``, which is always ``postgresql+psycopg``;
see ``migrations/env.py``):

- **M1** (empty database): implicit -- the upgrade to 0021 below starts from nothing.
- **M2** (existing PatientReferences): seeds several legacy rows across two provider
  organizations, then asserts every one is linked to a Patient after upgrading.
- **M3** (duplicate demographics): two legacy references share byte-identical ``display_name`` --
  asserts they resolve to **two distinct** Patients, never merged.
- **M4** (cross-provider same source-identity value): two different providers' legacy references
  both used the raw value ``"LEGACY-0"`` -- asserts two distinct Patients, isolated by provider.
- **M5** (null snapshots): a legacy reference with no ``display_name``/contact -- asserts the
  upgrade succeeds and no demographic is fabricated (its Patient's own fields stay ``NULL`` too).
- **M6** (tenant FK safety): asserts every backfilled Patient's ``provider_organization_id``
  matches its own reference's ``source_organization_id`` -- the real-database proof of §8's
  composite-FK invariant (also proven directly, as a rejected write, by
  ``tests/integration/test_dhidentity001_postgres_qualification.py``).
- **M7** (``current == heads == EXPECTED_HEAD``): covered by the adjacent CI step ("Migration
  history reaches head on a fresh database"), which runs immediately after this script in the same
  job against the same now-migrated database -- not duplicated here.

Also exercises §41's downgrade guard: after creating one native Patient with a recorded
``date_of_birth``, ``alembic downgrade`` past 0022 must refuse and must leave the schema unchanged.

**Manual / CI-wired only** -- reads connection settings from the same ``ADAPTER_DB_*`` environment
variables the application and the "Migration history reaches head" CI step already use. Intended to
run against the job's own disposable ``postgres:16-alpine`` service, immediately before that step,
in the same job -- never against a facility database.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.orm import sessionmaker

from daher_adapter.config import Settings
from daher_adapter.exchange.membership import ExchangeIdentityRepository
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.patients import PatientRepository

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config() -> Config:
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    return cfg


def _require(condition: bool, message: str) -> None:
    """Plain ``if``/``raise``, not a bare ``assert`` -- a qualification script's checks must run
    even if this script is ever invoked under ``python -O`` (which strips ``assert``)."""
    if not condition:
        raise AssertionError(message)


def main() -> int:
    settings = Settings.from_env()
    engine = create_engine(settings.database_url)
    cfg = _alembic_config()

    print("== upgrading fresh database to 0021_order_intent_ownership ==")
    command.upgrade(cfg, "0021_order_intent_ownership")

    prefix = f"m0022-{uuid.uuid4().hex[:8]}"
    provider_a = uuid.uuid4()
    provider_b = uuid.uuid4()
    legacy_refs = [
        # M2/M3: five references under provider_a, two sharing identical demographics.
        {
            "id": uuid.uuid4(),
            "source_organization_id": provider_a,
            "source_identity_value": "LEGACY-0",
            "display_name": "Duplicate Name",
        },
        {
            "id": uuid.uuid4(),
            "source_organization_id": provider_a,
            "source_identity_value": "LEGACY-1",
            "display_name": "Duplicate Name",
        },
        {
            "id": uuid.uuid4(),
            "source_organization_id": provider_a,
            "source_identity_value": "LEGACY-2",
            # M5: no display_name/contact at all.
            "display_name": None,
        },
        {
            "id": uuid.uuid4(),
            "source_organization_id": provider_a,
            "source_identity_value": "LEGACY-3",
            "display_name": "Patient Three",
        },
        {
            "id": uuid.uuid4(),
            "source_organization_id": provider_a,
            "source_identity_value": "LEGACY-4",
            "display_name": "Patient Four",
        },
        # M4: provider_b reuses the exact same raw source_identity_value as provider_a's first row.
        {
            "id": uuid.uuid4(),
            "source_organization_id": provider_b,
            "source_identity_value": "LEGACY-0",
            "display_name": "Provider B Patient",
        },
    ]

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO exchange_organizations (id, org_type, name, status, created_at) "
                "VALUES (:id, 'PROVIDER', :name, 'ACTIVE', now())"
            ),
            [
                {"id": provider_a, "name": f"{prefix}-provider-a"},
                {"id": provider_b, "name": f"{prefix}-provider-b"},
            ],
        )
        for ref in legacy_refs:
            conn.execute(
                text(
                    "INSERT INTO exchange_patient_references "
                    "(id, source_organization_id, source_system, source_identity_system, "
                    " source_identity_value, display_name, contact_channel_type, contact_value, "
                    " created_at) "
                    "VALUES (:id, :source_organization_id, :source_system, "
                    " :source_identity_system, :source_identity_value, :display_name, "
                    " NULL, NULL, now())"
                ),
                {
                    **ref,
                    "source_system": "legacy",
                    "source_identity_system": f"urn:{prefix}:legacy",
                },
            )

    legacy_count = len(legacy_refs)
    print(f"Seeded {legacy_count} legacy exchange_patient_references rows across 2 providers")

    # "head" runs 0022_exchange_patient_directory plus whatever purely-additive migrations later
    # tickets (e.g. DH-CLINIC-006's 0023_provider_lab_routes) have since stacked on top of it.
    print("== upgrading to head (runs 0022_exchange_patient_directory and later) ==")
    command.upgrade(cfg, "head")

    legacy_ids = [r["id"] for r in legacy_refs]

    with engine.connect() as conn:
        null_patient_id_count = conn.execute(
            text(
                "SELECT COUNT(*) FROM exchange_patient_references "
                "WHERE id IN :ids AND patient_id IS NULL"
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": legacy_ids},
        ).scalar_one()

        mismatched_tenant_count = conn.execute(
            text(
                "SELECT COUNT(*) FROM exchange_patient_references pr "
                "JOIN exchange_patients p ON p.id = pr.patient_id "
                "WHERE pr.id IN :ids "
                "AND p.provider_organization_id != pr.source_organization_id"
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": legacy_ids},
        ).scalar_one()

        fabricated_dob_count = conn.execute(
            text(
                "SELECT COUNT(*) FROM exchange_patient_references pr "
                "JOIN exchange_patients p ON p.id = pr.patient_id "
                "WHERE pr.id IN :ids AND p.date_of_birth IS NOT NULL"
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": legacy_ids},
        ).scalar_one()

        duplicate_demo_patient_ids = (
            conn.execute(
                text(
                    "SELECT DISTINCT p.id FROM exchange_patients p "
                    "JOIN exchange_patient_references pr ON pr.patient_id = p.id "
                    "WHERE pr.source_identity_value IN ('LEGACY-0', 'LEGACY-1') "
                    "AND pr.source_organization_id = :provider_a"
                ),
                {"provider_a": provider_a},
            )
            .scalars()
            .all()
        )

        cross_provider_patient_ids = (
            conn.execute(
                text(
                    "SELECT DISTINCT p.id FROM exchange_patients p "
                    "JOIN exchange_patient_references pr ON pr.patient_id = p.id "
                    "WHERE pr.source_identity_value = 'LEGACY-0'"
                )
            )
            .scalars()
            .all()
        )

        null_snapshot_patient = conn.execute(
            text(
                "SELECT p.display_name FROM exchange_patients p "
                "JOIN exchange_patient_references pr ON pr.patient_id = p.id "
                "WHERE pr.source_identity_value = 'LEGACY-2'"
            )
        ).scalar_one()

    _require(null_patient_id_count == 0, "every legacy reference must have a non-null patient_id")
    _require(
        mismatched_tenant_count == 0,
        "M6: every backfilled Patient's provider_organization_id must equal its own reference's "
        "source_organization_id",
    )
    _require(fabricated_dob_count == 0, "M5: a backfilled Patient must never have an invented DOB")
    _require(
        len(duplicate_demo_patient_ids) == 2,
        f"M3: two references with identical demographics must yield two distinct Patients, got "
        f"{duplicate_demo_patient_ids!r}",
    )
    _require(
        len(cross_provider_patient_ids) == 2,
        f"M4: two providers reusing the same raw source_identity_value must yield two distinct "
        f"Patients, got {cross_provider_patient_ids!r}",
    )
    _require(
        null_snapshot_patient is None, "M5: no display_name must be fabricated during backfill"
    )

    print(f"M1-M6 backfill assertions passed. Legacy references used: {legacy_count}")

    print("== §41 downgrade-guard qualification ==")
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    organizations = OrganizationRepository(sessions)
    identities = ExchangeIdentityRepository(sessions)
    patients = PatientRepository(sessions, organizations)
    guard_provider = organizations.create(org_type="PROVIDER", name=f"{prefix}-guard-provider")
    guard_identity, _ = identities.get_or_create(
        issuer="migration-qualification", subject=f"{prefix}-guard-actor"
    )
    patients.create(
        provider_organization_id=guard_provider.id,
        idempotency_key=f"{prefix}-guard-key",
        display_name="Downgrade Guard Patient",
        date_of_birth=None,
        contact_channel_type=None,
        contact_value=None,
        created_by_identity_id=guard_identity.id,
    )

    # DH-CLINIC-006 added 0023_provider_lab_routes on top of 0022 -- "head" no longer *is* 0022, so
    # the single-step downgrade this qualification actually cares about (0022 -> 0021, refused)
    # must be isolated from 0023's own downgrade first. 0023 -> 0022 is expected to succeed
    # cleanly here (this script never creates a ProviderLabRoute row), and Alembic runs a multi-
    # revision downgrade as one enclosing transaction -- if this step were skipped and 0022 ->
    # 0021's refusal raised inside the same `command.downgrade(cfg, "0021_...")` call as 0023 ->
    # 0022, the whole batch would roll back together, leaving the schema at 0023 instead of the
    # 0022 this test asserts.
    command.downgrade(cfg, "0022_exchange_patient_directory")
    with engine.connect() as conn:
        current_before_guard = conn.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
    _require(
        current_before_guard == "0022_exchange_patient_directory",
        f"expected schema at 0022 before exercising its own downgrade guard, got "
        f"{current_before_guard!r}",
    )

    try:
        command.downgrade(cfg, "0021_order_intent_ownership")
    except Exception as error:  # noqa: BLE001 -- checking for the specific refusal message
        _require(
            "refusing to downgrade past 0022" in str(error),
            f"downgrade failed for the wrong reason: {error!r}",
        )
    else:
        raise AssertionError(
            "downgrade past 0022 must be refused once any native patient registration exists"
        )

    with engine.connect() as conn:
        current_after_refusal = conn.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
    _require(
        current_after_refusal == "0022_exchange_patient_directory",
        f"a refused downgrade must leave the schema at 0022 unchanged, got "
        f"{current_after_refusal!r}",
    )
    print("Downgrade-guard qualification passed -- refused as expected, schema unchanged.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
