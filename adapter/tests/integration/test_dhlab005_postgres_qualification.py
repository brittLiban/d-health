"""DH-LAB-005 real PostgreSQL qualification: the concurrent Native controlled correction race
(pre-release), correction-vs-original-release, amendment-correction-vs-amendment-release, and
two-releasers-racing-the-same-amendment. SQLite is not sufficient evidence for production
PostgreSQL row-locking behavior -- matching the reasoning every other file in this directory
already established, applied here to this ticket's new
``LabResultRepository.correct_native_value``/``LabResultAmendmentRepository.correct_native_value``/
``LabResultAmendmentRepository.release_amendment`` methods, all of which serialize through the
identical ``LabOrder FOR UPDATE`` lock DH-LAB-004's own races already qualified.

**Manual / opt-in only**, mirroring every other live-infra integration test in this directory:
skipped by default (including in CI) unless every ``DHLAB005_PG_*`` env var below is set. Never
point this at a real facility database -- nothing here checks that; use a disposable instance.

Usage::

    DHLAB005_PG_HOST=127.0.0.1 \\
    DHLAB005_PG_PORT=5432 \\
    DHLAB005_PG_USER=dhlab005 \\
    DHLAB005_PG_PASSWORD=dhlab005 \\
    DHLAB005_PG_DATABASE=dhlab005_qual \\
        pytest adapter/tests/integration/test_dhlab005_postgres_qualification.py -v

The target database must already have migrations applied through ``0025_native_result_binding``
(``alembic upgrade head``) before running this file -- it does not run migrations itself.
"""

from __future__ import annotations

import os
import threading
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from daher_adapter.exchange.catalog import CatalogRepository
from daher_adapter.exchange.directory import LabDirectoryProfileRepository
from daher_adapter.exchange.identity import (
    PatientReferenceRepository,
    PractitionerReferenceRepository,
)
from daher_adapter.exchange.lab_result_templates import (
    LabResultTemplateRepository,
    TemplateObservationDefinitionInput,
)
from daher_adapter.exchange.lab_workflow import LabWorkflowRepository
from daher_adapter.exchange.membership import ExchangeIdentityRepository
from daher_adapter.exchange.models import (
    LabOrder,
    LabPrice,
    LabResultAmendment,
    LabResultReleaseVersion,
)
from daher_adapter.exchange.models import LabResultRelease as LabResultReleaseModel
from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.results import (
    AmendmentNotPreparing,
    InvalidResultSubmissionState,
    LabResultAmendmentRepository,
    LabResultReleaseRepository,
    LabResultRepository,
    NativeValueInput,
    StaleObservationVersion,
    StaleResultFingerprint,
)
from daher_adapter.exchange.selection import LabSelectionRepository

PG_HOST = os.environ.get("DHLAB005_PG_HOST")
PG_PORT = os.environ.get("DHLAB005_PG_PORT")
PG_USER = os.environ.get("DHLAB005_PG_USER")
PG_PASSWORD = os.environ.get("DHLAB005_PG_PASSWORD")
PG_DATABASE = os.environ.get("DHLAB005_PG_DATABASE")

pytestmark = pytest.mark.skipif(
    not (PG_HOST and PG_PORT and PG_USER and PG_PASSWORD and PG_DATABASE),
    reason="requires a live disposable PostgreSQL database (DHLAB005_PG_* env vars) -- never in CI",
)


@pytest.fixture
def sessions() -> sessionmaker:
    url = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}"
    engine = create_engine(url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _labtech_identity_id(sessions: sessionmaker, subject: str):
    identity, _ = ExchangeIdentityRepository(sessions).get_or_create(
        issuer="https://pglab005-qualification.example", subject=subject
    )
    return identity.id


def _seed_in_progress_native_item(sessions: sessionmaker, *, key_prefix: str):
    """One provider, one lab, a single-item order, selected, driven to IN_PROGRESS, with an
    active Native template on the item's offering. Returns
    ``(lab_id, order_id, item_id, template, definition_id)``."""
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    directory = LabDirectoryProfileRepository(sessions, organizations)
    patients = PatientReferenceRepository(sessions)
    practitioners = PractitionerReferenceRepository(sessions)
    orders = LabOrderRepository(sessions, organizations, catalog)
    selections = LabSelectionRepository(sessions, organizations)
    workflow = LabWorkflowRepository(sessions, organizations)
    templates = LabResultTemplateRepository(sessions)

    provider = organizations.create(org_type="PROVIDER", name=f"{key_prefix}-clinic")
    lab = organizations.create(org_type="LAB", name=f"{key_prefix}-lab")
    entry, _ = catalog.create_entry(code=f"{key_prefix}-TEST", display_name=f"{key_prefix} test")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("100.00"),
        currency="DJF",
        expected_current_price_id=None,
    )
    directory.upsert(
        lab_organization_id=lab.id,
        public_address="addr",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )
    patient = patients.get_or_create(
        source_organization_id=provider.id,
        source_system="pglab005-qualification",
        source_identity_system=f"urn:{key_prefix}:patient",
        source_identity_value="P-1",
    )
    practitioner = practitioners.get_or_create(
        source_organization_id=provider.id,
        source_system="pglab005-qualification",
        source_identity_system=f"urn:{key_prefix}:practitioner",
        source_identity_value="D-1",
    )
    order = orders.create(
        idempotency_key=f"{key_prefix}-order",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=[OrderItemInput(catalog_entry_id=entry.id)],
    ).order

    with sessions() as session:
        price_id = session.scalar(
            select(LabPrice.id).where(
                LabPrice.lab_offering_id == offering.id, LabPrice.superseded_at.is_(None)
            )
        )

    selections.select(
        lab_order_id=order.id, lab_organization_id=lab.id, expected_price_ids=frozenset([price_id])
    )
    workflow.accept(order_id=order.id, lab_organization_id=lab.id)
    workflow.record_collection(order_id=order.id, lab_organization_id=lab.id)
    workflow.start_processing(order_id=order.id, lab_organization_id=lab.id)
    order = orders.get(order.id)
    item_id = order.items[0].id

    template, _ = templates.create_template(
        lab_offering_id=offering.id,
        version=1,
        observations=[
            TemplateObservationDefinitionInput(
                source_code="A",
                display_label="Measure A",
                result_type="NUMERIC",
                numeric_unit="unit",
                required=True,
                sort_order=0,
            )
        ],
    )
    templates.activate_template(
        lab_offering_id=offering.id, template_id=template.id, expected_current_template_id=None
    )
    return lab.id, order.id, item_id, template, template.observations[0].id


def _seed_released_native_item(sessions: sessionmaker, *, key_prefix: str):
    """Extends `_seed_in_progress_native_item` through a recorded value, entry completion, and
    a Version-1 release. Returns ``(lab_id, order_id, item_id, original_observation_id)``."""
    lab_id, order_id, item_id, template, definition_id = _seed_in_progress_native_item(
        sessions, key_prefix=key_prefix
    )
    results = LabResultRepository(sessions)
    releases = LabResultReleaseRepository(sessions)

    write = results.record_native_values(
        order_id=order_id,
        lab_order_item_id=item_id,
        lab_organization_id=lab_id,
        template_id=template.id,
        values=[
            NativeValueInput(
                definition_id=definition_id, numeric_value=Decimal("1"), text_value=None
            )
        ],
        recorded_by_identity_id=_labtech_identity_id(sessions, f"{key_prefix}-tech"),
    )
    original_observation_id = write.values[0].observation_id
    results.complete_entry(
        order_id=order_id,
        lab_order_item_id=item_id,
        lab_organization_id=lab_id,
        completed_by_identity_id=_labtech_identity_id(sessions, f"{key_prefix}-tech"),
    )
    inspection = results.get_for_lab(order_id, lab_id)
    releases.release(
        order_id=order_id,
        lab_organization_id=lab_id,
        released_by_identity_id=_labtech_identity_id(sessions, f"{key_prefix}-releaser1"),
        expected_result_fingerprint=inspection.result_fingerprint,
    )
    return lab_id, order_id, item_id, original_observation_id


# -- PG-PC1: concurrent Native corrections of the same effective observation ----------------------


def test_pgpc1_concurrent_native_corrections_exactly_one_wins(sessions: sessionmaker) -> None:
    """Two concurrent callers Native-correct the identical predecessor observation with
    different new values. `correct_native_value` locks the same `LabOrder FOR UPDATE` first, so
    exactly one genuinely runs first; the other must see `StaleObservationVersion` -- never
    last-write-wins, never two effective successors for the same predecessor."""
    key_prefix = f"pglab005-pc1-{uuid.uuid4().hex[:8]}"
    lab_id, order_id, item_id, template, definition_id = _seed_in_progress_native_item(
        sessions, key_prefix=key_prefix
    )
    results = LabResultRepository(sessions)
    write = results.record_native_values(
        order_id=order_id,
        lab_order_item_id=item_id,
        lab_organization_id=lab_id,
        template_id=template.id,
        values=[
            NativeValueInput(
                definition_id=definition_id, numeric_value=Decimal("1"), text_value=None
            )
        ],
        recorded_by_identity_id=_labtech_identity_id(sessions, f"{key_prefix}-tech"),
    )
    observation_id = write.values[0].observation_id

    outcomes: dict[str, object] = {}
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def attempt(value: str, key: str) -> None:
        try:
            barrier.wait(timeout=15)
            outcomes[key] = results.correct_native_value(
                order_id=order_id,
                lab_order_item_id=item_id,
                lab_organization_id=lab_id,
                observation_id=observation_id,
                corrected_by_identity_id=_labtech_identity_id(sessions, f"{key_prefix}-{key}"),
                numeric_value=Decimal(value),
            )
        except StaleObservationVersion as conflict:
            outcomes[key] = conflict
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    t1 = threading.Thread(target=attempt, args=("2", "a"))
    t2 = threading.Thread(target=attempt, args=("3", "b"))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert not errors, f"no unexpected exception: {errors!r}"
    winners = [k for k, v in outcomes.items() if not isinstance(v, StaleObservationVersion)]
    losers = [k for k, v in outcomes.items() if isinstance(v, StaleObservationVersion)]
    assert len(winners) == 1 and len(losers) == 1, (
        f"exactly one correction must win, the other must see a stale predecessor: {outcomes!r}"
    )
    with sessions() as session:
        from daher_adapter.exchange.models import LabObservation

        count = session.scalar(
            select(func.count())
            .select_from(LabObservation)
            .where(
                LabObservation.supersedes_observation_id == observation_id,
                LabObservation.superseded_at.is_(None),
            )
        )
        assert count == 1, "exactly one effective successor, never two"


# -- PG-PC2: Native correction races the original release -----------------------------------------


def test_pgpc2_native_correction_races_original_release(sessions: sessionmaker) -> None:
    """A releaser inspects a completed order (capturing its fingerprint), then a technician
    Native-corrects the one effective observation while a release attempt using the now-stale
    fingerprint is in flight. Both `correct_native_value` and `release` lock the identical
    `LabOrder FOR UPDATE` first. If the correction wins, the stale-fingerprint release must be
    refused. If the release wins instead, the correction (now against a COMPLETED order) must be
    refused by the pre-existing IN_PROGRESS-only gate."""
    key_prefix = f"pglab005-pc2-{uuid.uuid4().hex[:8]}"
    lab_id, order_id, item_id, template, definition_id = _seed_in_progress_native_item(
        sessions, key_prefix=key_prefix
    )
    results = LabResultRepository(sessions)
    releases = LabResultReleaseRepository(sessions)

    write = results.record_native_values(
        order_id=order_id,
        lab_order_item_id=item_id,
        lab_organization_id=lab_id,
        template_id=template.id,
        values=[
            NativeValueInput(
                definition_id=definition_id, numeric_value=Decimal("1"), text_value=None
            )
        ],
        recorded_by_identity_id=_labtech_identity_id(sessions, f"{key_prefix}-tech"),
    )
    observation_id = write.values[0].observation_id
    results.complete_entry(
        order_id=order_id,
        lab_order_item_id=item_id,
        lab_organization_id=lab_id,
        completed_by_identity_id=_labtech_identity_id(sessions, f"{key_prefix}-tech"),
    )
    stale_fingerprint = results.get_for_lab(order_id, lab_id).result_fingerprint

    outcomes: dict[str, object] = {}
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def do_correct() -> None:
        try:
            barrier.wait(timeout=15)
            outcomes["correct"] = results.correct_native_value(
                order_id=order_id,
                lab_order_item_id=item_id,
                lab_organization_id=lab_id,
                observation_id=observation_id,
                corrected_by_identity_id=_labtech_identity_id(sessions, f"{key_prefix}-tech2"),
                numeric_value=Decimal("2"),
            )
        except InvalidResultSubmissionState as refused:
            outcomes["correct"] = refused
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    def do_release() -> None:
        try:
            barrier.wait(timeout=15)
            outcomes["release"] = releases.release(
                order_id=order_id,
                lab_organization_id=lab_id,
                released_by_identity_id=_labtech_identity_id(sessions, f"{key_prefix}-releaser"),
                expected_result_fingerprint=stale_fingerprint,
            )
        except StaleResultFingerprint as refused:
            outcomes["release"] = refused
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    t1 = threading.Thread(target=do_correct)
    t2 = threading.Thread(target=do_release)
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert not errors, f"no unexpected exception: {errors!r}"
    assert "correct" in outcomes and "release" in outcomes

    with sessions() as session:
        order = session.get(LabOrder, order_id)
        release_row = session.scalar(
            select(LabResultReleaseModel).where(LabResultReleaseModel.lab_order_id == order_id)
        )

    if isinstance(outcomes["release"], StaleResultFingerprint):
        assert release_row is None, "a stale-fingerprint release must never create a release row"
        assert order.status == "IN_PROGRESS"
    else:
        assert release_row is not None
        assert order.status == "COMPLETED"
        assert isinstance(outcomes["correct"], InvalidResultSubmissionState), (
            f"release's lock won first, so the correction must be refused: {outcomes['correct']!r}"
        )


# -- PG-AR1: amendment Native correction races amendment release ----------------------------------


def test_pgar1_amendment_native_correction_races_amendment_release(sessions: sessionmaker) -> None:
    """A releaser inspects a PREPARING amendment with one existing revision (capturing its
    fingerprint), then a technician Native-corrects the amendment's observation again while a
    release attempt using that now-stale fingerprint is in flight. Both `correct_native_value`
    (amendment-scoped) and `release_amendment` lock the identical `LabOrder FOR UPDATE` first. If
    the correction wins, the stale-fingerprint release must be refused and the amendment must
    remain PREPARING. If the release wins instead, the correction (now against a RELEASED
    amendment) must be refused by `AmendmentNotPreparing` -- never releasing a result set the
    releaser never actually saw."""
    key_prefix = f"pglab005-ar1-{uuid.uuid4().hex[:8]}"
    lab_id, order_id, item_id, original_observation_id = _seed_released_native_item(
        sessions, key_prefix=key_prefix
    )
    amendments = LabResultAmendmentRepository(sessions)
    amendment = amendments.start_amendment(
        order_id=order_id,
        lab_organization_id=lab_id,
        started_by_identity_id=_labtech_identity_id(sessions, f"{key_prefix}-tech"),
    )
    first_correction = amendments.correct_native_value(
        amendment_id=amendment.id,
        order_id=order_id,
        lab_order_item_id=item_id,
        lab_organization_id=lab_id,
        observation_id=original_observation_id,
        corrected_by_identity_id=_labtech_identity_id(sessions, f"{key_prefix}-tech"),
        numeric_value=Decimal("2"),
    )
    stale_fingerprint = amendments.get_amendment_inspection(amendment.id).result_fingerprint

    outcomes: dict[str, object] = {}
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def do_correct() -> None:
        try:
            barrier.wait(timeout=15)
            outcomes["correct"] = amendments.correct_native_value(
                amendment_id=amendment.id,
                order_id=order_id,
                lab_order_item_id=item_id,
                lab_organization_id=lab_id,
                observation_id=first_correction.id,
                corrected_by_identity_id=_labtech_identity_id(sessions, f"{key_prefix}-tech2"),
                numeric_value=Decimal("3"),
            )
        except AmendmentNotPreparing as refused:
            outcomes["correct"] = refused
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    def do_release() -> None:
        try:
            barrier.wait(timeout=15)
            outcomes["release"] = amendments.release_amendment(
                amendment_id=amendment.id,
                order_id=order_id,
                lab_organization_id=lab_id,
                released_by_identity_id=_labtech_identity_id(sessions, f"{key_prefix}-releaser"),
                expected_result_fingerprint=stale_fingerprint,
            )
        except StaleResultFingerprint as refused:
            outcomes["release"] = refused
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    t1 = threading.Thread(target=do_correct)
    t2 = threading.Thread(target=do_release)
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert not errors, f"no unexpected exception: {errors!r}"
    assert "correct" in outcomes and "release" in outcomes

    with sessions() as session:
        amendment_row = session.get(LabResultAmendment, amendment.id)
        version_count = session.scalar(
            select(func.count())
            .select_from(LabResultReleaseVersion)
            .where(LabResultReleaseVersion.lab_order_id == order_id)
        )

    if isinstance(outcomes["release"], StaleResultFingerprint):
        assert amendment_row.status == "PREPARING"
        assert version_count == 1, "only the original Version 1 -- the stale release must not land"
    else:
        assert amendment_row.status == "RELEASED"
        assert version_count == 2
        assert isinstance(outcomes["correct"], AmendmentNotPreparing), (
            f"release's lock won first, so the correction must be refused: {outcomes['correct']!r}"
        )


# -- PG-AR2: two releasers racing to release the same amendment -----------------------------------


def test_pgar2_two_releasers_racing_the_same_amendment_exactly_one_release(
    sessions: sessionmaker,
) -> None:
    """Two releasers concurrently call `release_amendment` for the identical PREPARING amendment
    with the identical (correct, freshly-inspected) fingerprint. `release_amendment` locks the
    same `LabOrder FOR UPDATE` first, so exactly one genuinely creates the release version row;
    the other's call must observe the identical already-created version as a safe idempotent
    replay -- never two Version-2 rows, never two different released-by identities recorded."""
    key_prefix = f"pglab005-ar2-{uuid.uuid4().hex[:8]}"
    lab_id, order_id, item_id, original_observation_id = _seed_released_native_item(
        sessions, key_prefix=key_prefix
    )
    amendments = LabResultAmendmentRepository(sessions)
    amendment = amendments.start_amendment(
        order_id=order_id,
        lab_organization_id=lab_id,
        started_by_identity_id=_labtech_identity_id(sessions, f"{key_prefix}-tech"),
    )
    amendments.correct_native_value(
        amendment_id=amendment.id,
        order_id=order_id,
        lab_order_item_id=item_id,
        lab_organization_id=lab_id,
        observation_id=original_observation_id,
        corrected_by_identity_id=_labtech_identity_id(sessions, f"{key_prefix}-tech"),
        numeric_value=Decimal("2"),
    )
    fingerprint = amendments.get_amendment_inspection(amendment.id).result_fingerprint

    outcomes: dict[str, object] = {}
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def do_release(key: str) -> None:
        try:
            barrier.wait(timeout=15)
            outcomes[key] = amendments.release_amendment(
                amendment_id=amendment.id,
                order_id=order_id,
                lab_organization_id=lab_id,
                released_by_identity_id=_labtech_identity_id(sessions, f"{key_prefix}-{key}"),
                expected_result_fingerprint=fingerprint,
            )
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    t1 = threading.Thread(target=do_release, args=("releaser1",))
    t2 = threading.Thread(target=do_release, args=("releaser2",))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert not errors, (
        f"no unexpected exception (a fresh, correct fingerprint must never conflict): {errors!r}"
    )
    assert "releaser1" in outcomes and "releaser2" in outcomes
    record_a, created_a = outcomes["releaser1"]
    record_b, created_b = outcomes["releaser2"]
    created_flags = sorted([created_a, created_b])
    assert created_flags == [False, True], (
        f"exactly one caller must have actually created the release, the other must replay: "
        f"{created_a=} {created_b=}"
    )
    assert record_a.id == record_b.id, "both callers must observe the identical release version"

    with sessions() as session:
        amendment_row = session.get(LabResultAmendment, amendment.id)
        version_count = session.scalar(
            select(func.count())
            .select_from(LabResultReleaseVersion)
            .where(LabResultReleaseVersion.lab_order_id == order_id)
        )
        assert amendment_row.released_by_identity_id in (
            _labtech_identity_id(sessions, f"{key_prefix}-releaser1"),
            _labtech_identity_id(sessions, f"{key_prefix}-releaser2"),
        ), "the fact must be who actually released it first, never overwritten by the loser"
        assert version_count == 2, "exactly one Version 1 + one Version 2, never a duplicate"
