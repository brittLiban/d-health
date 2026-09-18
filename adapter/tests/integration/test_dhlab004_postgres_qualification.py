"""DH-LAB-004 real PostgreSQL qualification: the first-write template-pinning race, concurrent
Native batch value writes, and the release-vs-correction fingerprint race. SQLite is not
sufficient evidence for production PostgreSQL row-locking behavior -- matching the reasoning
every other file in this directory already established, applied here to
``exchange/results.py``'s new ``LabOffering FOR UPDATE``-serialized first-write pin and the
``LabOrder FOR UPDATE``-serialized fingerprint re-check inside release.

**Manual / opt-in only**, mirroring every other live-infra integration test in this directory:
skipped by default (including in CI) unless every ``DHLAB004_PG_*`` env var below is set. Never
point this at a real facility database -- nothing here checks that; use a disposable instance.

Usage::

    DHLAB004_PG_HOST=127.0.0.1 \\
    DHLAB004_PG_PORT=5432 \\
    DHLAB004_PG_USER=dhlab004 \\
    DHLAB004_PG_PASSWORD=dhlab004 \\
    DHLAB004_PG_DATABASE=dhlab004_qual \\
        pytest adapter/tests/integration/test_dhlab004_postgres_qualification.py -v

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
    LabObservation,
    LabOrder,
    LabPrice,
    LabResult,
    LabResultType,
)
from daher_adapter.exchange.models import LabResultRelease as LabResultReleaseModel
from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.exchange.results import (
    InvalidResultSubmissionState,
    LabResultReleaseRepository,
    LabResultRepository,
    NativeValueInput,
    NoActiveTemplateForOffering,
    ObservationValueConflict,
    StaleResultFingerprint,
    compute_result_fingerprint,
)
from daher_adapter.exchange.selection import LabSelectionRepository

PG_HOST = os.environ.get("DHLAB004_PG_HOST")
PG_PORT = os.environ.get("DHLAB004_PG_PORT")
PG_USER = os.environ.get("DHLAB004_PG_USER")
PG_PASSWORD = os.environ.get("DHLAB004_PG_PASSWORD")
PG_DATABASE = os.environ.get("DHLAB004_PG_DATABASE")

pytestmark = pytest.mark.skipif(
    not (PG_HOST and PG_PORT and PG_USER and PG_PASSWORD and PG_DATABASE),
    reason="requires a live disposable PostgreSQL database (DHLAB004_PG_* env vars) -- never in CI",
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
        issuer="https://pglab004-qualification.example", subject=subject
    )
    return identity.id


def _seed_in_progress_order_with_offering(sessions: sessionmaker, *, key_prefix: str):
    """One provider, one lab, a single-item order, selected and driven to IN_PROGRESS -- returns
    ``(lab_id, order_id, item_id, offering_id)`` so callers can configure/activate a template
    against the exact offering this item resolves to."""
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)
    directory = LabDirectoryProfileRepository(sessions, organizations)
    patients = PatientReferenceRepository(sessions)
    practitioners = PractitionerReferenceRepository(sessions)
    orders = LabOrderRepository(sessions, organizations, catalog)
    selections = LabSelectionRepository(sessions, organizations)
    workflow = LabWorkflowRepository(sessions, organizations)

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
        source_system="pglab004-qualification",
        source_identity_system=f"urn:{key_prefix}:patient",
        source_identity_value="P-1",
    )
    practitioner = practitioners.get_or_create(
        source_organization_id=provider.id,
        source_system="pglab004-qualification",
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
    return lab.id, order.id, order.items[0].id, offering.id


def _numeric_def(source_code: str, sort_order: int, required: bool = True):
    return TemplateObservationDefinitionInput(
        source_code=source_code,
        display_label=f"Measure {source_code}",
        result_type="NUMERIC",
        numeric_unit="unit",
        required=required,
        sort_order=sort_order,
    )


# -- PG-T1: template-activation-vs-first-write race -----------------------------------------------


def test_pgt1_activation_vs_first_write_never_produces_a_mismatched_pin(
    sessions: sessionmaker,
) -> None:
    """A `LAB_TECH`'s first Native write races an `ORG_ADMIN` activating version 2 of the same
    offering's template. Both `record_native_values`'s first-write path and `activate_template`
    lock the identical `LabOffering FOR UPDATE` first, so exactly one genuinely runs first --
    never a container pinned to a template that was not actually active at the locked instant it
    was created, and never a template row left in an ambiguous active state."""
    key_prefix = f"pglab004-t1-{uuid.uuid4().hex[:8]}"
    lab_id, order_id, item_id, offering_id = _seed_in_progress_order_with_offering(
        sessions, key_prefix=key_prefix
    )
    templates = LabResultTemplateRepository(sessions)
    results = LabResultRepository(sessions)

    v1, _ = templates.create_template(
        lab_offering_id=offering_id, version=1, observations=[_numeric_def("A", 0)]
    )
    templates.activate_template(
        lab_offering_id=offering_id, template_id=v1.id, expected_current_template_id=None
    )
    v2, _ = templates.create_template(
        lab_offering_id=offering_id, version=2, observations=[_numeric_def("A", 0)]
    )

    outcomes: dict[str, object] = {}
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def do_write() -> None:
        try:
            barrier.wait(timeout=15)
            outcomes["write"] = results.record_native_values(
                order_id=order_id,
                lab_order_item_id=item_id,
                lab_organization_id=lab_id,
                template_id=v1.id,
                values=[
                    NativeValueInput(
                        definition_id=v1.observations[0].id,
                        numeric_value=Decimal("1"),
                        text_value=None,
                    )
                ],
                recorded_by_identity_id=_labtech_identity_id(sessions, f"{key_prefix}-tech"),
            )
        except NoActiveTemplateForOffering as refused:
            outcomes["write"] = refused
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    def do_activate() -> None:
        try:
            barrier.wait(timeout=15)
            outcomes["activate"] = templates.activate_template(
                lab_offering_id=offering_id,
                template_id=v2.id,
                expected_current_template_id=v1.id,
            )
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    t1 = threading.Thread(target=do_write)
    t2 = threading.Thread(target=do_activate)
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert not errors, f"no unexpected exception: {errors!r}"
    assert "write" in outcomes and "activate" in outcomes

    with sessions() as session:
        container = session.scalar(select(LabResult).where(LabResult.lab_order_item_id == item_id))

    if isinstance(outcomes["write"], NoActiveTemplateForOffering):
        # Activation won the lock first -- v1 was no longer active by the time the write's own
        # lock was granted. The write must have created NO container at all (fail closed, never a
        # container pinned to a template that was not active at the locked instant).
        assert container is None
    else:
        # The write won the lock first -- v1 was still active, so the pin is v1, and it must
        # survive activation's later switch to v2 without being altered.
        assert container is not None
        assert container.result_template_id == v1.id


# -- PG-E1/PG-E2: concurrent Native batch value writes ---------------------------------------------


def test_pge1_concurrent_identical_batch_converges_no_duplicate(sessions: sessionmaker) -> None:
    """Ten concurrent callers submit the exact identical value for the same definition on the
    same item's first write. The `LabOrder FOR UPDATE` lock serializes container creation to
    exactly one winner; every other caller's insert-first-wins IntegrityError fallback re-reads
    the just-created container and finds its own value already recorded -- a safe replay, not a
    conflict. Exactly one observation must exist afterward, never ten."""
    key_prefix = f"pglab004-e1-{uuid.uuid4().hex[:8]}"
    lab_id, order_id, item_id, offering_id = _seed_in_progress_order_with_offering(
        sessions, key_prefix=key_prefix
    )
    templates = LabResultTemplateRepository(sessions)
    results = LabResultRepository(sessions)
    template, _ = templates.create_template(
        lab_offering_id=offering_id, version=1, observations=[_numeric_def("A", 0)]
    )
    templates.activate_template(
        lab_offering_id=offering_id, template_id=template.id, expected_current_template_id=None
    )
    definition_id = template.observations[0].id

    n = 10
    errors: list[BaseException] = []
    lock = threading.Lock()
    barrier = threading.Barrier(n)

    def attempt(i: int) -> None:
        try:
            barrier.wait(timeout=15)
            results.record_native_values(
                order_id=order_id,
                lab_order_item_id=item_id,
                lab_organization_id=lab_id,
                template_id=template.id,
                values=[
                    NativeValueInput(
                        definition_id=definition_id, numeric_value=Decimal("7.5"), text_value=None
                    )
                ],
                recorded_by_identity_id=_labtech_identity_id(sessions, f"{key_prefix}-tech-{i}"),
            )
        except BaseException as error:  # noqa: BLE001
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"an identical-value replay must never be a conflict: {errors!r}"
    with sessions() as session:
        container = session.scalar(select(LabResult).where(LabResult.lab_order_item_id == item_id))
        count = session.scalar(
            select(func.count())
            .select_from(LabObservation)
            .where(LabObservation.lab_result_id == container.id)
        )
        assert count == 1, "no lost write, no duplicate -- exactly one effective observation"


def test_pge2_concurrent_different_values_exactly_one_wins(sessions: sessionmaker) -> None:
    """Two concurrent callers submit *different* values for the same definition on the same
    item's first write. Exactly one must win (create the observation); the other must see
    `ObservationValueConflict` (or, if it wins container creation but the other's value already
    landed, the same conflict on its own replay check) -- never last-write-wins, never two
    effective observations for the identical definition (the partial unique index's own backstop
    would also refuse that at the database level if application logic somehow let it through)."""
    key_prefix = f"pglab004-e2-{uuid.uuid4().hex[:8]}"
    lab_id, order_id, item_id, offering_id = _seed_in_progress_order_with_offering(
        sessions, key_prefix=key_prefix
    )
    templates = LabResultTemplateRepository(sessions)
    results = LabResultRepository(sessions)
    template, _ = templates.create_template(
        lab_offering_id=offering_id, version=1, observations=[_numeric_def("A", 0)]
    )
    templates.activate_template(
        lab_offering_id=offering_id, template_id=template.id, expected_current_template_id=None
    )
    definition_id = template.observations[0].id

    outcomes: dict[str, object] = {}
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def attempt(value: str, key: str) -> None:
        try:
            barrier.wait(timeout=15)
            outcomes[key] = results.record_native_values(
                order_id=order_id,
                lab_order_item_id=item_id,
                lab_organization_id=lab_id,
                template_id=template.id,
                values=[
                    NativeValueInput(
                        definition_id=definition_id, numeric_value=Decimal(value), text_value=None
                    )
                ],
                recorded_by_identity_id=_labtech_identity_id(sessions, f"{key_prefix}-{key}"),
            )
        except ObservationValueConflict as conflict:
            outcomes[key] = conflict
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    t1 = threading.Thread(target=attempt, args=("1.0", "a"))
    t2 = threading.Thread(target=attempt, args=("2.0", "b"))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert not errors, f"no unexpected exception: {errors!r}"
    winners = [k for k, v in outcomes.items() if not isinstance(v, ObservationValueConflict)]
    losers = [k for k, v in outcomes.items() if isinstance(v, ObservationValueConflict)]
    assert len(winners) == 1 and len(losers) == 1, (
        f"exactly one caller must win and the other must conflict, never both succeeding with "
        f"different values (last-write-wins) and never both failing: {outcomes!r}"
    )
    with sessions() as session:
        container = session.scalar(select(LabResult).where(LabResult.lab_order_item_id == item_id))
        count = session.scalar(
            select(func.count())
            .select_from(LabObservation)
            .where(LabObservation.lab_result_id == container.id)
        )
        assert count == 1, "the loser's value must never have been written alongside the winner's"


# -- PG-REL: release-vs-correction fingerprint race ------------------------------------------------


def test_pgrel_correction_winning_the_lock_invalidates_a_concurrent_stale_release(
    sessions: sessionmaker,
) -> None:
    """A releaser inspects a completed order (capturing its fingerprint), then a technician
    corrects the one effective observation while a release attempt using the now-stale
    fingerprint is in flight. Both `correct_observation` and `release` lock the identical
    `LabOrder FOR UPDATE` first, so exactly one genuinely runs first. If the correction wins,
    the stale-fingerprint release must be refused (`StaleResultFingerprint`) and no
    `LabResultRelease` row may exist afterward -- the releaser must never release an observation
    set they did not actually see. If the release wins instead (fingerprint still fresh at that
    exact instant), it must succeed, and the correction (now against a released order) must
    itself be refused by the unrelated, pre-existing IN_PROGRESS-only gate."""
    key_prefix = f"pglab004-rel-{uuid.uuid4().hex[:8]}"
    lab_id, order_id, item_id, _offering_id = _seed_in_progress_order_with_offering(
        sessions, key_prefix=key_prefix
    )
    results = LabResultRepository(sessions)
    releases = LabResultReleaseRepository(sessions)

    observation = results.record_observation(
        order_id=order_id,
        lab_order_item_id=item_id,
        lab_organization_id=lab_id,
        result_type=LabResultType.NUMERIC,
        source_code="X",
        display_label_snapshot="X",
        recorded_by_identity_id=_labtech_identity_id(sessions, f"{key_prefix}-tech"),
        numeric_value=Decimal("1"),
    )
    results.complete_entry(
        order_id=order_id,
        lab_order_item_id=item_id,
        lab_organization_id=lab_id,
        completed_by_identity_id=_labtech_identity_id(sessions, f"{key_prefix}-tech"),
    )
    stale_fingerprint = compute_result_fingerprint([(item_id, observation.id)])

    outcomes: dict[str, object] = {}
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def do_correct() -> None:
        try:
            barrier.wait(timeout=15)
            outcomes["correct"] = results.correct_observation(
                order_id=order_id,
                lab_order_item_id=item_id,
                lab_organization_id=lab_id,
                observation_id=observation.id,
                result_type=LabResultType.NUMERIC,
                source_code="X",
                display_label_snapshot="X",
                corrected_by_identity_id=_labtech_identity_id(sessions, f"{key_prefix}-tech2"),
                numeric_value=Decimal("2"),
            )
        except InvalidResultSubmissionState as refused:
            # Expected when release wins the lock first: the order is COMPLETED by the time
            # correction acquires the lock, and the pre-existing IN_PROGRESS-only gate (unrelated
            # to this ticket's own fingerprint mechanism) refuses it.
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
        # Correction won the lock first -- the release's stale fingerprint was correctly refused.
        assert release_row is None, "a stale-fingerprint release must never create a release row"
        assert order.status == "IN_PROGRESS"
    else:
        # Release won the lock first -- it succeeded against the still-fresh fingerprint, and the
        # correction (now racing a COMPLETED order) was refused by the pre-existing IN_PROGRESS
        # gate, unrelated to this ticket's own fingerprint mechanism.
        assert release_row is not None
        assert order.status == "COMPLETED"
        assert isinstance(outcomes["correct"], InvalidResultSubmissionState), (
            "release's lock won first, so the order was already COMPLETED by the time "
            f"correction acquired the lock -- it must have been refused: {outcomes['correct']!r}"
        )
