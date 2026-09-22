"""DH-CONNECT-CONTRACT-001 §64 real PostgreSQL qualification: the connector installation and
qualification-evidence schema, exercised against a live PostgreSQL instance rather than SQLite.

SQLite is not sufficient evidence here, and for this ticket the reason is unusually concrete rather
than the generic "production is PostgreSQL" argument every other file in this directory makes:

*   ``uq_connector_installations_one_live_per_org`` is a **partial** unique index
    (``WHERE status <> 'RETIRED'``). SQLite does support partial indexes, but the predicate's
    interaction with the ``VARCHAR``/``text`` comparison is a PostgreSQL detail, and this index is
    the single structural thing standing between a facility and two simultaneously live connector
    installations. If it silently did not apply, every SQLite test in this ticket would still pass.
*   ``0027_connect_contract`` **drops and recreates** the two audit-vocabulary CHECK constraints on
    ``exchange_security_audit_events``. SQLite has no ``ALTER TABLE ... DROP CONSTRAINT``, so that
    migration branch is literally never executed by the SQLite suite.
*   Every new foreign key is ``ON DELETE RESTRICT``. That is the property that makes qualification
    evidence durable -- you cannot tidy away a facility and take its failed attempts with it.

**Manual / opt-in only**, mirroring every other live-infra integration test in this directory:
skipped by default (including in CI) unless every ``DHCONNECT001_PG_*`` env var below is set. Never
point this at a real facility database -- nothing here checks that; use a disposable instance.

Usage::

    DHCONNECT001_PG_HOST=127.0.0.1 \\
    DHCONNECT001_PG_PORT=5432 \\
    DHCONNECT001_PG_USER=dhconnect001 \\
    DHCONNECT001_PG_PASSWORD=dhconnect001 \\
    DHCONNECT001_PG_DATABASE=dhconnect001_qual \\
        pytest adapter/tests/integration/test_dhconnect001_postgres_qualification.py -v

The target database must already have migrations applied through ``0027_connect_contract``
(``alembic upgrade head``) before running this file -- it does not run migrations itself.

No credential belonging to any external EMR/LIS appears here or is written anywhere by this file.
The fingerprints below are SHA-256 digests of synthetic, non-secret configuration shapes.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from daher_adapter.connect.contract import Capability, fingerprint
from daher_adapter.connect.models import (
    ConnectorInstallation,
    ConnectorInstallationStatus,
    ConnectorQualification,
    ConnectorQualificationCapability,
    EvidenceLevel,
    QualificationResult,
)
from daher_adapter.connect.qualification import (
    QUALIFICATION_SUITE_VERSION,
    QualificationGap,
    qualification_verdict_in_session,
)
from daher_adapter.connect.repository import (
    CapabilityOutcome,
    ConnectorAlreadyInstalled,
    ConnectorInstallationRepository,
    FacilityNotConnect,
    InstallationRetired,
)
from daher_adapter.exchange.facility_enrollment import FacilityEnrollmentRepository
from daher_adapter.exchange.membership import ExchangeIdentityRepository
from daher_adapter.exchange.models import (
    OnboardingStatus,
    OrganizationType,
    ParticipationMode,
)
from daher_adapter.exchange.organizations import OrganizationRepository

PG_HOST = os.environ.get("DHCONNECT001_PG_HOST")
PG_PORT = os.environ.get("DHCONNECT001_PG_PORT")
PG_USER = os.environ.get("DHCONNECT001_PG_USER")
PG_PASSWORD = os.environ.get("DHCONNECT001_PG_PASSWORD")
PG_DATABASE = os.environ.get("DHCONNECT001_PG_DATABASE")

pytestmark = pytest.mark.skipif(
    not (PG_HOST and PG_PORT and PG_USER and PG_PASSWORD and PG_DATABASE),
    reason=(
        "requires a live disposable PostgreSQL database (DHCONNECT001_PG_* env vars) -- never in CI"
    ),
)

CONFIG_FP = fingerprint({"endpoint": "https://lis.invalid/fhir", "timeout_s": 30})
MAPPING_FP = fingerprint({"mapping_revision": [["synthetic-lis", "GLU", "cat-1", "GLU"]]})

LAB_BUNDLE = (
    Capability.ORDER_RECEIPT,
    Capability.BUSINESS_ACCEPTANCE,
    Capability.COLLECTION_EVENT,
    Capability.RELEASED_RESULT_PUBLICATION,
    Capability.AMENDMENT_PUBLICATION,
)


@pytest.fixture(scope="module")
def engine():
    url = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
    eng = create_engine(url, future=True)
    with eng.connect() as conn:
        conn.execute(text("SELECT 1"))
    yield eng
    eng.dispose()


@pytest.fixture
def sessions(engine):
    return sessionmaker(engine, expire_on_commit=False, future=True)


@pytest.fixture
def repos(sessions):
    return {
        "sessions": sessions,
        "organizations": OrganizationRepository(sessions),
        "enrollments": FacilityEnrollmentRepository(sessions),
        "identities": ExchangeIdentityRepository(sessions),
        "connectors": ConnectorInstallationRepository(sessions),
    }


def _actor(repos) -> uuid.UUID:
    identity, _ = repos["identities"].get_or_create(
        issuer="https://issuer.example",
        subject=f"platform-admin-{uuid.uuid4()}",
        display_name="Opérateur",
        email=None,
    )
    return identity.id


def _lab(repos, *, connect: bool = True):
    """A laboratory with a unique name, since this database is not torn down between tests."""
    org = repos["organizations"].create(
        name=f"Laboratoire {uuid.uuid4().hex[:8]}",
        org_type=OrganizationType.LAB.value,
    )
    repos["enrollments"].create(
        organization_id=org.id,
        participation_mode=(ParticipationMode.CONNECT if connect else ParticipationMode.NATIVE),
        onboarding_status=(
            OnboardingStatus.AWAITING_CONNECTOR if connect else OnboardingStatus.DRAFT
        ),
    )
    return org


def _register(repos, organization_id, **overrides):
    payload = {
        "organization_id": organization_id,
        "connector_key": "openelis-fhir",
        "connector_version": "0.1",
        "source_system": "OpenELIS",
        "interface_type": "FHIR",
        "configuration_fingerprint": CONFIG_FP,
        "mapping_fingerprint": MAPPING_FP,
        "actor_identity_id": _actor(repos),
    }
    payload.update(overrides)
    return repos["connectors"].register(**payload)


def _qualify(
    repos,
    installation_id,
    *,
    capabilities=LAB_BUNDLE,
    result=QualificationResult.PASSED,
    evidence=EvidenceLevel.EXERCISED,
):
    """``evidence`` defaults to EXERCISED because these tests are about storage and constraints.

    PG8's subject is "a genuinely qualified installation goes stale when its configuration
    changes", which needs a qualified starting point. A fixture constructing exercised evidence,
    not a claim that any run produced one -- the synthetic laboratory harness cannot exercise
    ORDER_RECEIPT, and the ES suite pins that. ``CapabilityOutcome`` has no default of its own.
    """
    started = datetime.now(UTC) - timedelta(minutes=5)
    return repos["connectors"].record_qualification(
        installation_id=installation_id,
        suite_version=QUALIFICATION_SUITE_VERSION,
        started_at=started,
        completed_at=started + timedelta(minutes=4),
        result=result,
        capabilities=[
            CapabilityOutcome(capability=c, passed=True, evidence_level=evidence)
            for c in capabilities
        ],
        actor_identity_id=_actor(repos),
        evidence_summary="qualification PostgreSQL réelle",
    )


# -- PG1: the schema itself ------------------------------------------------------------------


def test_PG1_the_migration_produced_the_schema_the_model_expects(engine):
    """The tables, the partial index, and the CHECK constraints exist on real PostgreSQL.

    Read from ``pg_catalog`` rather than from SQLAlchemy's metadata: metadata describes what the
    code believes, and the whole point of this file is to check what the migration actually built.
    """
    with engine.connect() as conn:
        tables = set(
            conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name LIKE 'connector%'"
                )
            ).scalars()
        )
        assert tables == {
            "connector_installations",
            "connector_qualifications",
            "connector_qualification_capabilities",
        }

        index_def = conn.execute(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname = 'uq_connector_installations_one_live_per_org'"
            )
        ).scalar_one()
        assert "UNIQUE" in index_def
        assert "RETIRED" in index_def, "the partial predicate did not survive into PostgreSQL"

        checks = dict(
            conn.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE contype = 'c' AND conrelid::regclass::text LIKE 'connector%'"
                )
            ).all()
        )
        capability_check = checks["ck_connector_qualification_capabilities_capability"]
        for capability in Capability:
            assert capability.value in capability_check, (
                f"{capability.value} is a contract capability the database would refuse to store"
            )
        assert "PASSED" in checks["ck_connector_qualifications_result"]
        assert "FAILED" in checks["ck_connector_qualifications_result"]


def test_PG2_the_audit_vocabulary_swap_actually_ran(engine):
    """§39: the six connector event types and the new resource type are accepted by the CHECKs.

    This migration branch does not exist under SQLite (no ``DROP CONSTRAINT``), so this assertion
    has no SQLite equivalent anywhere in the suite -- if the swap were wrong, nothing else would
    catch it until a connector registration failed in production.
    """
    with engine.connect() as conn:
        defs = " ".join(
            conn.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE contype = 'c' "
                    "AND conrelid = 'exchange_security_audit_events'::regclass"
                )
            ).scalars()
        )
    for event in (
        "connector_registered",
        "connector_suspended",
        "connector_restored",
        "connector_retired",
        "connector_configuration_changed",
        "connector_qualification_recorded",
    ):
        assert event in defs, f"{event} would be rejected by the audit CHECK constraint"
    assert "connector_installation" in defs


# -- PG3-PG5: what the database refuses ------------------------------------------------------


def test_PG3_a_facility_cannot_hold_two_live_installations(repos):
    """The partial unique index, exercised through the repository and then around it.

    Two assertions, deliberately. The repository refusal is the message an operator sees; the
    ``IntegrityError`` is what happens if some future code path forgets to ask the repository. The
    second is the one that still holds when the first is edited away.
    """
    lab = _lab(repos)
    first = _register(repos, lab.id)

    with pytest.raises(ConnectorAlreadyInstalled):
        _register(repos, lab.id, connector_key="other-connector")

    with pytest.raises(IntegrityError):
        with repos["sessions"].begin() as session:
            session.add(
                ConnectorInstallation(
                    organization_id=lab.id,
                    connector_key="smuggled",
                    connector_version="0.1",
                    contract_version="1.0",
                    source_system="OpenELIS",
                    interface_type="FHIR",
                    configuration_fingerprint=CONFIG_FP,
                    mapping_fingerprint=MAPPING_FP,
                    status=ConnectorInstallationStatus.REGISTERED.value,
                )
            )

    # ...and the retirement escape hatch works, which is the reason the index is partial at all.
    repos["connectors"].retire(installation_id=first.id, actor_identity_id=_actor(repos))
    replacement = _register(repos, lab.id, connector_key="replacement-connector")
    assert replacement.id != first.id


def test_PG4_the_database_refuses_states_the_contract_does_not_define(repos):
    """CHECK constraints, not just enums in Python. An invalid status never reaches disk."""
    lab = _lab(repos)
    with pytest.raises(IntegrityError):
        with repos["sessions"].begin() as session:
            session.add(
                ConnectorInstallation(
                    organization_id=lab.id,
                    connector_key="openelis-fhir",
                    connector_version="0.1",
                    contract_version="1.0",
                    source_system="OpenELIS",
                    interface_type="FHIR",
                    configuration_fingerprint=CONFIG_FP,
                    mapping_fingerprint=MAPPING_FP,
                    status="QUALIFIED",  # the column this schema deliberately does not have
                )
            )

    installation = _register(repos, lab.id)
    qualification = _qualify(repos, installation.id)

    with pytest.raises(IntegrityError):
        with repos["sessions"].begin() as session:
            session.add(
                ConnectorQualificationCapability(
                    qualification_id=qualification.id,
                    capability="EVERYTHING",
                    passed=True,
                    evidence_level=EvidenceLevel.EXERCISED.value,
                )
            )

    with pytest.raises(IntegrityError):
        with repos["sessions"].begin() as session:
            session.add(
                ConnectorQualificationCapability(
                    qualification_id=qualification.id,
                    capability=Capability.ORDER_RECEIPT.value,  # already recorded by _qualify
                    passed=False,
                    evidence_level=EvidenceLevel.EXERCISED.value,
                )
            )


def test_PG5_a_non_connect_facility_cannot_have_a_connector(repos):
    """§7: NATIVE facilities are untouched by this ticket, and the refusal is the repository's.

    Worth stating plainly: the *schema* would accept this row -- ``organization_id`` is just a
    foreign key. The participation-mode rule lives in the repository, which is why it is asserted
    here rather than left to a constraint that does not exist.
    """
    native = _lab(repos, connect=False)
    with pytest.raises(FacilityNotConnect):
        _register(repos, native.id)

    with repos["sessions"]() as session:
        assert (
            session.scalars(
                select(ConnectorInstallation).where(
                    ConnectorInstallation.organization_id == native.id
                )
            ).first()
            is None
        )


# -- PG6-PG8: evidence durability -------------------------------------------------------------


def test_PG6_qualification_evidence_survives_everything_that_could_tidy_it_away(repos, engine):
    """§31: attempts accumulate; nothing rewrites or deletes one; retirement does not erase.

    A failed attempt followed by a passing one must leave *two* rows. An operator asking "has this
    connector ever failed" is asking a question the system has to be able to answer honestly, and
    a schema that overwrote the failure would answer it wrongly and confidently.
    """
    lab = _lab(repos)
    installation = _register(repos, lab.id)

    failed = _qualify(
        repos,
        installation.id,
        capabilities=[c for c in LAB_BUNDLE if c is not Capability.AMENDMENT_PUBLICATION],
        result=QualificationResult.FAILED,
    )
    passed = _qualify(repos, installation.id)

    history = repos["connectors"].qualification_history(installation.id)
    assert [h.id for h in history] == [passed.id, failed.id], "newest first, both present"
    assert {h.result for h in history} == {
        QualificationResult.PASSED,
        QualificationResult.FAILED,
    }

    # ON DELETE RESTRICT: the facility cannot be removed out from under its own evidence.
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM exchange_organizations WHERE id = :oid"), {"oid": lab.id}
            )

    # Retirement is a lifecycle state, not an eraser.
    repos["connectors"].retire(installation_id=installation.id, actor_identity_id=_actor(repos))
    assert len(repos["connectors"].qualification_history(installation.id)) == 2
    with pytest.raises(InstallationRetired):
        _qualify(repos, installation.id)


def test_PG7_qualification_evidence_is_bound_to_one_facility_only(repos):
    """§9/S4: laboratory A's qualification says nothing whatsoever about laboratory B.

    Checked at the storage layer here, where the binding is a foreign key rather than a comparison
    somebody could remove. AC8 covers the same property on live traffic.
    """
    lab_a = _lab(repos)
    lab_b = _lab(repos)
    installation_a = _register(repos, lab_a.id)
    _qualify(repos, installation_a.id)

    with repos["sessions"]() as session:
        verdict_a = qualification_verdict_in_session(session, lab_a.id, OrganizationType.LAB)
        verdict_b = qualification_verdict_in_session(session, lab_b.id, OrganizationType.LAB)
    assert verdict_a.qualified is True
    assert verdict_b.qualified is False
    assert verdict_b.gap is QualificationGap.NO_INSTALLATION

    with repos["sessions"]() as session:
        rows = session.scalars(
            select(ConnectorQualification)
            .join(
                ConnectorInstallation,
                ConnectorInstallation.id == ConnectorQualification.connector_installation_id,
            )
            .where(ConnectorInstallation.organization_id == lab_b.id)
        ).all()
    assert rows == [], "laboratory B has evidence it never earned"


def test_PG8_a_configuration_change_invalidates_the_stored_qualification(repos):
    """§30 on real PostgreSQL: the fingerprints pinned in the evidence row are what make it stale.

    The qualification row is untouched -- it still says PASSED, truthfully, about a configuration
    that is no longer the one installed. The *verdict* is what changes, and that separation is the
    whole design: evidence is a historical fact, qualification is a present-tense question.
    """
    lab = _lab(repos)
    installation = _register(repos, lab.id)
    qualification = _qualify(repos, installation.id)

    with repos["sessions"]() as session:
        assert qualification_verdict_in_session(session, lab.id, OrganizationType.LAB).qualified

    repos["connectors"].update_fingerprints(
        installation_id=installation.id,
        configuration_fingerprint=fingerprint({"endpoint": "https://lis.invalid/fhir2"}),
        actor_identity_id=_actor(repos),
    )

    with repos["sessions"]() as session:
        verdict = qualification_verdict_in_session(session, lab.id, OrganizationType.LAB)
        stored = session.get(ConnectorQualification, qualification.id)
    assert verdict.qualified is False
    assert verdict.gap is QualificationGap.QUALIFICATION_STALE
    assert stored.result == QualificationResult.PASSED.value, "the evidence was rewritten"
    assert stored.configuration_fingerprint == CONFIG_FP


# -- PG9-PG11: the evidence-level column (DH-CONNECT-CONTRACT-001 correction) ------------------


def test_PG9_the_evidence_level_column_is_constrained_on_real_postgresql(engine):
    """Migration 0028 on real PostgreSQL: non-null, CHECK-limited, no server default.

    The absence of a default is the part worth asserting. A default would mean a row that forgot
    to say how it was evidenced quietly inherits an answer, and since the verdict reads this
    column, the inherited answer would be the one that gates clinical routing.
    """
    with engine.connect() as conn:
        column = conn.execute(
            text(
                "SELECT is_nullable, column_default, character_maximum_length "
                "FROM information_schema.columns "
                "WHERE table_name = 'connector_qualification_capabilities' "
                "AND column_name = 'evidence_level'"
            )
        ).one()
        assert column.is_nullable == "NO"
        assert column.column_default is None, "a default would let a forgotten level be inherited"
        assert column.character_maximum_length == 16

        check = conn.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'ck_connector_qualification_capabilities_evidence_level'"
            )
        ).scalar_one()
    assert "DECLARED" in check
    assert "EXERCISED" in check
    # Exactly two levels. A third would be a judgement call dressed as data, and every such call
    # is somebody's opportunity to round upward.
    for invented in ("PARTIAL", "STAGING", "PROBABLY", "ASSUMED"):
        assert invented not in check


def test_PG10_an_invalid_or_missing_evidence_level_is_refused(repos):
    """Enforced by the database, not only by the Python enum.

    Both directions: a level the contract does not define, and no level at all. The second is the
    one a future code path is likelier to produce, by adding a row and not knowing this column
    exists.
    """
    lab = _lab(repos)
    installation = _register(repos, lab.id)
    qualification = _qualify(repos, installation.id)

    with pytest.raises(IntegrityError):
        with repos["sessions"].begin() as session:
            session.add(
                ConnectorQualificationCapability(
                    qualification_id=qualification.id,
                    capability=Capability.PROCESSING_EVENT.value,
                    passed=True,
                    evidence_level="PROBABLY",
                )
            )

    with pytest.raises(IntegrityError):
        with repos["sessions"].begin() as session:
            session.add(
                ConnectorQualificationCapability(
                    qualification_id=qualification.id,
                    capability=Capability.PROCESSING_EVENT.value,
                    passed=True,
                )
            )


def test_PG11_declared_and_exercised_rows_survive_and_decide_the_verdict(repos):
    """The whole correction, round-tripped through real PostgreSQL.

    Two installations at two facilities, differing in exactly one column on one row. The one whose
    ORDER_RECEIPT is a declaration is not production-qualified; the one whose ORDER_RECEIPT was
    driven is -- and is still not routable, because `connector_operational` is a separate gate.
    """
    declared_lab = _lab(repos)
    declared_installation = _register(repos, declared_lab.id)
    repos["connectors"].record_qualification(
        installation_id=declared_installation.id,
        suite_version=QUALIFICATION_SUITE_VERSION,
        started_at=datetime.now(UTC) - timedelta(minutes=5),
        completed_at=datetime.now(UTC),
        result=QualificationResult.PASSED,
        capabilities=[
            CapabilityOutcome(
                capability=c,
                passed=True,
                evidence_level=(
                    EvidenceLevel.DECLARED
                    if c is Capability.ORDER_RECEIPT
                    else EvidenceLevel.EXERCISED
                ),
            )
            for c in LAB_BUNDLE
        ],
        actor_identity_id=_actor(repos),
        evidence_summary="déclaration seule pour la réception des ordres",
    )

    exercised_lab = _lab(repos)
    exercised_installation = _register(repos, exercised_lab.id)
    _qualify(repos, exercised_installation.id, evidence=EvidenceLevel.EXERCISED)

    with repos["sessions"]() as session:
        declared_verdict = qualification_verdict_in_session(
            session, declared_lab.id, OrganizationType.LAB
        )
        exercised_verdict = qualification_verdict_in_session(
            session, exercised_lab.id, OrganizationType.LAB
        )

    assert declared_verdict.qualified is False
    assert declared_verdict.gap is QualificationGap.REQUIRED_CAPABILITY_UNEXERCISED
    assert declared_verdict.unexercised_capabilities == (Capability.ORDER_RECEIPT,)
    assert exercised_verdict.qualified is True

    # The rows are what they were written as -- read back from the database, not from the objects.
    with repos["sessions"]() as session:
        levels = dict(
            session.execute(
                select(
                    ConnectorQualificationCapability.capability,
                    ConnectorQualificationCapability.evidence_level,
                )
                .join(
                    ConnectorQualification,
                    ConnectorQualification.id == ConnectorQualificationCapability.qualification_id,
                )
                .where(ConnectorQualification.connector_installation_id == declared_installation.id)
            ).all()
        )
    assert levels[Capability.ORDER_RECEIPT.value] == EvidenceLevel.DECLARED.value
    assert levels[Capability.BUSINESS_ACCEPTANCE.value] == EvidenceLevel.EXERCISED.value
