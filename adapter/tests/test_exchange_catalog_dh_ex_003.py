"""DH-EX-003: catalog/offering idempotency, price CAS semantics, and the whole-order eligibility
matrix -- the exact synthetic scenario the ticket specifies (Demo Clinic, Labs A-D, Tests A-C).
"""

from __future__ import annotations

import threading
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from exchange_support import make_lab, make_provider
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.catalog import (
    CatalogEntryConflict,
    CatalogRepository,
    OfferingConflict,
    OfferingInactiveConflict,
    PriceConflict,
)
from daher_adapter.exchange.models import Base, LabPrice
from daher_adapter.exchange.organizations import InvalidOrganizationType, OrganizationRepository


@pytest.fixture
def organizations(sessions: sessionmaker[Session]) -> OrganizationRepository:
    return OrganizationRepository(sessions)


@pytest.fixture
def catalog(
    sessions: sessionmaker[Session], organizations: OrganizationRepository
) -> CatalogRepository:
    return CatalogRepository(sessions, organizations)


def _current_price_id(sessions: sessionmaker[Session], offering_id) -> object:
    with sessions() as session:
        return session.scalar(
            select(LabPrice.id).where(
                LabPrice.lab_offering_id == offering_id, LabPrice.superseded_at.is_(None)
            )
        )


# -- catalog entry idempotency ---------------------------------------------------------------------


def test_catalog_entry_create_is_idempotent(catalog: CatalogRepository) -> None:
    first, first_created = catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")
    second, second_created = catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")
    assert first_created is True
    assert second_created is False
    assert first.id == second.id


def test_catalog_entry_create_conflicts_on_different_content(catalog: CatalogRepository) -> None:
    catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")
    with pytest.raises(CatalogEntryConflict):
        catalog.create_entry(code="DHEALTH-DEMO-A", display_name="A Completely Different Name")


def test_catalog_entries_default_synthetic(catalog: CatalogRepository) -> None:
    entry, _ = catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")
    assert entry.is_synthetic is True


# -- lab offering idempotency ----------------------------------------------------------------------


def test_offering_create_is_idempotent(
    catalog: CatalogRepository, organizations: OrganizationRepository
) -> None:
    lab = make_lab(organizations)
    entry, _ = catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")
    first, first_created = catalog.create_offering(
        lab_organization_id=lab.id, catalog_entry_id=entry.id, local_test_code="LOCAL-A"
    )
    second, second_created = catalog.create_offering(
        lab_organization_id=lab.id, catalog_entry_id=entry.id, local_test_code="LOCAL-A"
    )
    assert first_created is True
    assert second_created is False
    assert first.id == second.id


def test_offering_create_conflicts_on_different_local_test_code(
    catalog: CatalogRepository, organizations: OrganizationRepository
) -> None:
    lab = make_lab(organizations)
    entry, _ = catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")
    catalog.create_offering(
        lab_organization_id=lab.id, catalog_entry_id=entry.id, local_test_code="LOCAL-A"
    )
    with pytest.raises(OfferingConflict):
        catalog.create_offering(
            lab_organization_id=lab.id, catalog_entry_id=entry.id, local_test_code="DIFFERENT-CODE"
        )


def test_offering_create_never_silently_reactivates(
    catalog: CatalogRepository, organizations: OrganizationRepository
) -> None:
    lab = make_lab(organizations)
    entry, _ = catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    catalog.set_offering_active(offering.id, False)

    with pytest.raises(OfferingInactiveConflict):
        catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)


def test_provider_cannot_get_an_offering(
    catalog: CatalogRepository, organizations: OrganizationRepository
) -> None:
    provider = make_provider(organizations)
    entry, _ = catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")
    with pytest.raises(InvalidOrganizationType):
        catalog.create_offering(lab_organization_id=provider.id, catalog_entry_id=entry.id)


def test_set_offering_active_reports_unchanged_on_replay(
    catalog: CatalogRepository, organizations: OrganizationRepository
) -> None:
    lab = make_lab(organizations)
    entry, _ = catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)

    _, first_outcome = catalog.set_offering_active(offering.id, False)
    _, second_outcome = catalog.set_offering_active(offering.id, False)
    assert first_outcome == "updated"
    assert second_outcome == "unchanged"


# -- price CAS semantics ---------------------------------------------------------------------------


def test_first_price_requires_expected_none(
    catalog: CatalogRepository,
    organizations: OrganizationRepository,
    sessions: sessionmaker[Session],
) -> None:
    lab = make_lab(organizations)
    entry, _ = catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)

    record, outcome = catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("1000"),
        currency="DJF",
        expected_current_price_id=None,
    )
    assert outcome == "created"
    assert record.amount == Decimal("1000")


def test_first_price_with_a_stale_expected_id_is_a_conflict(
    catalog: CatalogRepository, organizations: OrganizationRepository
) -> None:
    lab = make_lab(organizations)
    entry, _ = catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)

    with pytest.raises(PriceConflict):
        catalog.set_price(
            offering_id=offering.id,
            amount=Decimal("1000"),
            currency="DJF",
            expected_current_price_id=uuid.uuid4(),
        )


def test_same_price_replay_with_stale_expected_id_is_unchanged_not_a_conflict(
    catalog: CatalogRepository, organizations: OrganizationRepository
) -> None:
    """The explicit exception the ticket calls out: if the actual current price already equals
    the requested amount/currency, that is a safe replay regardless of what
    `expected_current_price_id` said."""
    lab = make_lab(organizations)
    entry, _ = catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("1000"),
        currency="DJF",
        expected_current_price_id=None,
    )

    record, outcome = catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("1000"),
        currency="DJF",
        expected_current_price_id=uuid.uuid4(),  # deliberately wrong/stale token
    )
    assert outcome == "unchanged"
    assert record.amount == Decimal("1000")


def test_update_with_correct_expected_id_supersedes(
    catalog: CatalogRepository,
    organizations: OrganizationRepository,
    sessions: sessionmaker[Session],
) -> None:
    lab = make_lab(organizations)
    entry, _ = catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    first_price, _ = catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("1000"),
        currency="DJF",
        expected_current_price_id=None,
    )

    second_price, outcome = catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("1200"),
        currency="DJF",
        expected_current_price_id=first_price.id,
    )
    assert outcome == "updated"
    assert second_price.amount == Decimal("1200")

    with sessions() as session:
        superseded = session.get(LabPrice, first_price.id)
        assert superseded.superseded_at is not None


def test_update_with_wrong_expected_id_is_a_conflict(
    catalog: CatalogRepository, organizations: OrganizationRepository
) -> None:
    lab = make_lab(organizations)
    entry, _ = catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("1000"),
        currency="DJF",
        expected_current_price_id=None,
    )

    with pytest.raises(PriceConflict):
        catalog.set_price(
            offering_id=offering.id,
            amount=Decimal("1500"),  # differs from the actual current price
            currency="DJF",
            expected_current_price_id=uuid.uuid4(),
        )


def test_price_history_is_preserved_and_never_mutated(
    catalog: CatalogRepository,
    organizations: OrganizationRepository,
    sessions: sessionmaker[Session],
) -> None:
    lab = make_lab(organizations)
    entry, _ = catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    first_price, _ = catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("1000"),
        currency="DJF",
        expected_current_price_id=None,
    )
    catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("1200"),
        currency="DJF",
        expected_current_price_id=first_price.id,
    )

    with sessions() as session:
        historical = session.get(LabPrice, first_price.id)
        assert historical.amount == Decimal("1000"), "the historical row must never be mutated"
        count = session.scalar(
            select(func.count())
            .select_from(LabPrice)
            .where(LabPrice.lab_offering_id == offering.id)
        )
    assert count == 2


# -- whole-order eligibility: the exact ticket scenario --------------------------------------------


def _setup_scenario(catalog: CatalogRepository, organizations: OrganizationRepository, directory):
    """DHealth Demo Clinic / Labs A-D / Tests A-C, built exactly as DH-EX-003 specifies."""
    lab_a = make_lab(organizations, name="DHealth Demo Lab A")
    lab_b = make_lab(organizations, name="DHealth Demo Lab B")
    lab_c = make_lab(organizations, name="DHealth Demo Lab C")
    lab_d = make_lab(organizations, name="DHealth Demo Lab D")

    entry_a, _ = catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")
    entry_b, _ = catalog.create_entry(code="DHEALTH-DEMO-B", display_name="Demo Test B")
    entry_c, _ = catalog.create_entry(code="DHEALTH-DEMO-C", display_name="Demo Test C")

    def _offer_and_price(lab_id, entry_id, amount=Decimal("1000")):
        offering, _ = catalog.create_offering(lab_organization_id=lab_id, catalog_entry_id=entry_id)
        catalog.set_price(
            offering_id=offering.id,
            amount=amount,
            currency="DJF",
            expected_current_price_id=None,
        )
        return offering

    # Lab A: A+B+C, all active, all DJF, listed, accepting -- ELIGIBLE
    for entry in (entry_a, entry_b, entry_c):
        _offer_and_price(lab_a.id, entry.id)
    directory.upsert(
        lab_organization_id=lab_a.id,
        public_address="Lab A address",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )

    # Lab B: A+B only -- excluded (missing C)
    for entry in (entry_a, entry_b):
        _offer_and_price(lab_b.id, entry.id)
    directory.upsert(
        lab_organization_id=lab_b.id,
        public_address="Lab B address",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )

    # Lab C: A+B+C, but B has no current price -- excluded until priced
    _offer_and_price(lab_c.id, entry_a.id)
    offering_c_b, _ = catalog.create_offering(
        lab_organization_id=lab_c.id, catalog_entry_id=entry_b.id
    )
    _offer_and_price(lab_c.id, entry_c.id)
    directory.upsert(
        lab_organization_id=lab_c.id,
        public_address="Lab C address",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )

    # Lab D: A+B+C fully priced, listed, but NOT accepting orders -- excluded
    for entry in (entry_a, entry_b, entry_c):
        _offer_and_price(lab_d.id, entry.id)
    directory.upsert(
        lab_organization_id=lab_d.id,
        public_address="Lab D address",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=False,
    )

    return {
        "labs": {"a": lab_a, "b": lab_b, "c": lab_c, "d": lab_d},
        "entries": {"a": entry_a, "b": entry_b, "c": entry_c},
        "offering_c_b": offering_c_b,
        "offering_a_lab_a": None,
    }


@pytest.fixture
def directory(sessions, organizations):
    from daher_adapter.exchange.directory import LabDirectoryProfileRepository

    return LabDirectoryProfileRepository(sessions, organizations)


def test_whole_order_eligibility_only_lab_a_initially(
    catalog: CatalogRepository, organizations: OrganizationRepository, directory
) -> None:
    scenario = _setup_scenario(catalog, organizations, directory)
    entry_ids = [scenario["entries"][k].id for k in ("a", "b", "c")]

    results = catalog.find_whole_order_eligible_labs(entry_ids)

    assert [r.lab_organization_id for r in results] == [scenario["labs"]["a"].id]
    only = results[0]
    assert len(only.prices) == 3
    assert only.total_amount == Decimal("3000")
    assert only.currency == "DJF"


def test_whole_order_eligibility_no_partial_lab_ever(
    catalog: CatalogRepository, organizations: OrganizationRepository, directory
) -> None:
    scenario = _setup_scenario(catalog, organizations, directory)
    entry_ids = [scenario["entries"][k].id for k in ("a", "b", "c")]
    results = catalog.find_whole_order_eligible_labs(entry_ids)
    for lab in results:
        assert len(lab.prices) == len(entry_ids), "no lab result may be partial -- ALL tests only"


def test_whole_order_eligibility_after_completing_lab_c_price(
    catalog: CatalogRepository, organizations: OrganizationRepository, directory
) -> None:
    scenario = _setup_scenario(catalog, organizations, directory)
    entry_ids = [scenario["entries"][k].id for k in ("a", "b", "c")]

    # Complete Lab C's missing price for Test B.
    catalog.set_price(
        offering_id=scenario["offering_c_b"].id,
        amount=Decimal("1000"),
        currency="DJF",
        expected_current_price_id=None,
    )

    results = catalog.find_whole_order_eligible_labs(entry_ids)
    result_lab_ids = [r.lab_organization_id for r in results]
    assert set(result_lab_ids) == {scenario["labs"]["a"].id, scenario["labs"]["c"].id}
    # Sorted by total ascending, then name, then UUID -- both total 3000 DJF here, so name breaks
    # the tie: "DHealth Demo Lab A" < "DHealth Demo Lab C".
    assert result_lab_ids == [scenario["labs"]["a"].id, scenario["labs"]["c"].id]


def test_whole_order_eligibility_after_deactivating_lab_a_offering_b(
    catalog: CatalogRepository, organizations: OrganizationRepository, directory
) -> None:
    scenario = _setup_scenario(catalog, organizations, directory)
    entry_ids = [scenario["entries"][k].id for k in ("a", "b", "c")]
    catalog.set_price(
        offering_id=scenario["offering_c_b"].id,
        amount=Decimal("1000"),
        currency="DJF",
        expected_current_price_id=None,
    )

    # Deactivate Lab A's offering for Test B.
    offerings_a = catalog.list_offerings_for_lab(scenario["labs"]["a"].id)
    offering_a_b = next(o for o in offerings_a if o.catalog_entry_id == scenario["entries"]["b"].id)
    catalog.set_offering_active(offering_a_b.id, False)

    results = catalog.find_whole_order_eligible_labs(entry_ids)
    assert [r.lab_organization_id for r in results] == [scenario["labs"]["c"].id]


def test_whole_order_eligibility_unlisted_lab_excluded(
    catalog: CatalogRepository, organizations: OrganizationRepository, directory
) -> None:
    lab = make_lab(organizations)
    entry, _ = catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("1000"),
        currency="DJF",
        expected_current_price_id=None,
    )
    directory.upsert(
        lab_organization_id=lab.id,
        public_address="X",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=False,
        accepting_orders=True,
    )
    assert catalog.find_whole_order_eligible_labs([entry.id]) == []


def test_whole_order_eligibility_inactive_catalog_entry_excludes_everyone(
    catalog: CatalogRepository, organizations: OrganizationRepository, directory, sessions
) -> None:
    from daher_adapter.exchange.models import TestCatalogEntry

    lab = make_lab(organizations)
    entry, _ = catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("1000"),
        currency="DJF",
        expected_current_price_id=None,
    )
    directory.upsert(
        lab_organization_id=lab.id,
        public_address="X",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )
    with sessions() as session:
        row = session.get(TestCatalogEntry, entry.id)
        row.active = False
        session.commit()

    assert catalog.find_whole_order_eligible_labs([entry.id]) == []


def test_whole_order_eligibility_inactive_lab_organization_excluded(
    catalog: CatalogRepository, organizations: OrganizationRepository, directory, sessions
) -> None:
    lab = make_lab(organizations)
    entry, _ = catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("1000"),
        currency="DJF",
        expected_current_price_id=None,
    )
    directory.upsert(
        lab_organization_id=lab.id,
        public_address="X",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )
    assert len(catalog.find_whole_order_eligible_labs([entry.id])) == 1

    from daher_adapter.exchange.models import Organization

    with sessions() as session:
        org_row = session.get(Organization, lab.id)
        org_row.status = "INACTIVE"
        session.commit()

    assert catalog.find_whole_order_eligible_labs([entry.id]) == []


def test_whole_order_eligibility_non_synthetic_catalog_entry_excluded(
    catalog: CatalogRepository, organizations: OrganizationRepository, directory
) -> None:
    """A non-synthetic catalog entry must never be treated as pilot-approved marketplace
    inventory -- even one that bypasses the HTTP API's own hard-coded synthetic-only enforcement
    (defense in depth at the query level too)."""
    lab = make_lab(organizations)
    entry, _ = catalog.create_entry(
        code="DHEALTH-REAL-A", display_name="Not Synthetic", is_synthetic=False
    )
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("1000"),
        currency="DJF",
        expected_current_price_id=None,
    )
    directory.upsert(
        lab_organization_id=lab.id,
        public_address="X",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )
    assert catalog.find_whole_order_eligible_labs([entry.id]) == []


def test_whole_order_eligibility_empty_request_returns_empty(catalog: CatalogRepository) -> None:
    assert catalog.find_whole_order_eligible_labs([]) == []


def test_whole_order_eligibility_non_djf_current_price_excluded(
    catalog: CatalogRepository, organizations: OrganizationRepository, directory, sessions
) -> None:
    """DH-EX-003R Part O/Q: a non-DJF current price must never enter the DJF marketplace total,
    even if it reached `exchange_lab_prices` through something other than this HTTP API's
    DJF-only-`Literal` request schema (a direct repository call, a future endpoint, a data
    migration) -- `LabPrice.currency` is a plain code, not a hard enum, by design. This is also
    the regression test for the pass-(3) currency-filter gap DH-EX-003R found and fixed in
    `find_whole_order_eligible_labs`: pass (1) already excluded a non-DJF price from candidacy,
    but pass (3)'s actual line-fetch did not originally re-check currency, so a lab whose *other*
    tests were DJF-priced could still have had a non-DJF line silently summed into
    `total_amount`."""
    lab = make_lab(organizations)
    entry, _ = catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    directory.upsert(
        lab_organization_id=lab.id,
        public_address="X",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )
    # Bypass the HTTP schema's `currency: Literal["DJF"]` deliberately, at the repository layer,
    # to prove the query itself -- not just the API's request validation -- refuses a non-DJF
    # current price.
    catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("1000"),
        currency="USD",
        expected_current_price_id=None,
    )

    assert catalog.find_whole_order_eligible_labs([entry.id]) == []


# -- price snapshot non-regression -------------------------------------------------------------


def test_marketplace_price_change_never_rewrites_an_existing_order_snapshot(
    catalog: CatalogRepository, organizations: OrganizationRepository, sessions
) -> None:
    """DH-EX-003R permanent regression test: `LabOrderItem.selected_price_amount` is frozen at
    selection time (`exchange/selection.py`) and this ticket's marketplace/pricing work must never
    reach back and change it. A later `set_price` call changes what the *marketplace* reports as
    current, but an already-selected order's snapshot must stay exactly what the patient/provider
    actually saw and agreed to."""
    from exchange_support import make_patient_reference, make_practitioner_reference

    from daher_adapter.exchange.directory import LabDirectoryProfileRepository
    from daher_adapter.exchange.identity import (
        PatientReferenceRepository,
        PractitionerReferenceRepository,
    )
    from daher_adapter.exchange.orders import LabOrderRepository, OrderItemInput
    from daher_adapter.exchange.selection import LabSelectionRepository

    lab = make_lab(organizations)
    entry, _ = catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)
    catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("1000"),
        currency="DJF",
        expected_current_price_id=None,
    )
    directory = LabDirectoryProfileRepository(sessions, organizations)
    directory.upsert(
        lab_organization_id=lab.id,
        public_address="X",
        latitude=None,
        longitude=None,
        public_phone=None,
        listed=True,
        accepting_orders=True,
    )

    provider = make_provider(organizations)
    patients = PatientReferenceRepository(sessions)
    practitioners = PractitionerReferenceRepository(sessions)
    patient = make_patient_reference(patients, provider_org_id=provider.id)
    practitioner = make_practitioner_reference(practitioners, provider_org_id=provider.id)
    orders = LabOrderRepository(sessions, organizations, catalog)
    selections = LabSelectionRepository(sessions, organizations)

    order_result = orders.create(
        idempotency_key="snapshot-regression-1",
        provider_organization_id=provider.id,
        patient_reference_id=patient.id,
        practitioner_reference_id=practitioner.id,
        items=[OrderItemInput(catalog_entry_id=entry.id)],
    )
    price_id = _current_price_id(sessions, offering.id)
    selections.select(
        lab_order_id=order_result.order.id,
        lab_organization_id=lab.id,
        expected_price_ids=frozenset({price_id}),
    )

    selected_before = orders.get(order_result.order.id).items[0]
    assert selected_before.selected_price_amount == Decimal("1000")
    assert selected_before.selected_price_currency == "DJF"

    # The lab raises its price after the selection was made and frozen.
    current_price_id = _current_price_id(sessions, offering.id)
    _, outcome = catalog.set_price(
        offering_id=offering.id,
        amount=Decimal("1200"),
        currency="DJF",
        expected_current_price_id=current_price_id,
    )
    assert outcome == "updated"
    # Confirms the price actually changed for anyone reading current pricing going forward --
    # the point of this test is that the *already-selected* order below stays at the old value.
    assert _current_price_id(sessions, offering.id) != current_price_id

    # ...but the already-selected order's frozen snapshot is completely untouched.
    selected_after = orders.get(order_result.order.id).items[0]
    assert selected_after.selected_price_amount == Decimal("1000"), (
        "an already-selected order's price snapshot must never be rewritten by a later "
        "marketplace price change"
    )
    assert selected_after.selected_price_currency == "DJF"


# -- concurrency (required SQLite proof; real-Postgres proof is separate, not committed) -----------


def test_concurrent_same_price_produces_no_raw_integrity_error_and_one_effective_change(
    tmp_path: Path,
) -> None:
    # `set_price`'s retry loop issues a SAVEPOINT, and on a caught `IntegrityError` re-reads and
    # retries with *another* write in the same outer transaction -- exactly the pysqlite gap
    # `conftest.py`'s shared `engine` fixture documents and works around (plain pysqlite silently
    # breaks SAVEPOINT rollback when a completed/failed nested transaction is followed by more
    # writes in the same outer one). This test builds its own engine (to control the SQLite
    # busy-timeout, matching this file's other concurrency test's own rationale), so it needs the
    # same workaround applied directly rather than inheriting it from the shared fixture.
    # `BEGIN IMMEDIATE` (below) rather than a plain deferred `BEGIN` -- this test's transactions
    # always write, so acquiring SQLite's write lock immediately avoids connections racing to
    # upgrade from reader to writer, the specific pattern that otherwise surfaces `OperationalError:
    # database is locked` even within a configured busy-timeout. The real 25-way proof still runs
    # against real PostgreSQL separately (not committed, matching this repo's established
    # real-Postgres-qualification pattern).
    database = tmp_path / "exchange-price-concurrency.sqlite"
    engine = create_engine(f"sqlite:///{database}", connect_args={"timeout": 30})

    @event.listens_for(engine, "connect")
    def _disable_pysqlite_implicit_transactions(dbapi_connection, connection_record) -> None:  # noqa: ARG001
        dbapi_connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def _emit_explicit_begin(connection) -> None:
        # `BEGIN IMMEDIATE`, not a plain deferred `BEGIN`: this test's transactions always write,
        # so acquiring the write lock immediately avoids two connections both entering as readers
        # and then racing to upgrade -- the specific SQLite pattern that can surface
        # `OperationalError: database is locked` even within a configured busy-timeout.
        connection.exec_driver_sql("BEGIN IMMEDIATE")

    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    organizations = OrganizationRepository(sessions)
    catalog = CatalogRepository(sessions, organizations)

    lab = make_lab(organizations)
    entry, _ = catalog.create_entry(code="DHEALTH-DEMO-A", display_name="Demo Test A")
    offering, _ = catalog.create_offering(lab_organization_id=lab.id, catalog_entry_id=entry.id)

    thread_count = 10
    barrier = threading.Barrier(thread_count)
    outcomes: list[str] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def attempt() -> None:
        try:
            barrier.wait(timeout=10)
            _, outcome = catalog.set_price(
                offering_id=offering.id,
                amount=Decimal("1000"),
                currency="DJF",
                expected_current_price_id=None,
            )
            with lock:
                outcomes.append(outcome)
        except BaseException as error:  # noqa: BLE001 -- captured for the assertion
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=attempt) for _ in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, f"unexpected exceptions: {errors!r}"
    assert len(outcomes) == thread_count
    assert outcomes.count("created") == 1
    assert outcomes.count("unchanged") == thread_count - 1

    with sessions() as session:
        current_count = session.scalar(
            select(func.count())
            .select_from(LabPrice)
            .where(LabPrice.lab_offering_id == offering.id, LabPrice.superseded_at.is_(None))
        )
    assert current_count == 1
    engine.dispose()
