"""DHEALTH-RESULT-DELIVERY-001 / ADR-064: one authoritative owner per delivery-shaped fact.

DHealth has four facts that a careless reader collapses into the word "delivered":

    a released clinical result exists        -> LabResultReleaseVersion
    a clinician reviewed one exact version   -> DoctorReview
    a provider's own system received it      -> ConnectorDeliveryObligation / ConnectorDelivery
    the patient was messaged                 -> PatientNotification / its attempts

``ResultDelivery`` predates three of those four. It was created by ``0004_exchange_core`` as the
*only* delivery concept DHealth had, and ADR-043 then recorded its meaning as undecided with the
instruction that "a future ticket must decide before any delivery mechanism is implemented". A
delivery mechanism was implemented (Connect, ADR-057 and migration ``0031``) and that decision was
never made. ADR-064 makes it: **``ResultDelivery`` is deprecated, compatibility-only, and no new
runtime writes it.**

These are structural assertions on committed source, in the spirit of
``test_native_deployment_topology.py``. They cannot prove that nothing will ever mean the wrong
thing by "delivered"; what they stop is the specific, concrete way the boundary erodes -- a future
adapter, hook or notification path quietly advancing ``ResultDelivery`` so that two rows claim the
same fact and disagree. That is exactly the accident ADR-064 exists to prevent, and it is the
failure mode the first real Connect adapter is most likely to cause.

Read as text rather than by importing and introspecting: the invariant is about what the *source*
does, and a module that has to be imported to be checked can be made to pass by being clever at
import time.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from daher_adapter.connect.runtime_models import ConnectorDeliveryObligation
from daher_adapter.exchange.models import LabResultReleaseVersion, ResultDelivery

_SRC = Path(__file__).resolve().parents[1] / "src" / "daher_adapter"

#: The one module allowed to name the Exchange delivery repository: the module that defines it.
_DEFINING_MODULE = _SRC / "exchange" / "results.py"


def _production_sources() -> list[Path]:
    return sorted(p for p in _SRC.rglob("*.py") if "__pycache__" not in p.parts)


# -- the guard that matters ----------------------------------------------------------------------


def test_rd6_no_production_module_reaches_the_exchange_delivery_repository() -> None:
    """``ResultDeliveryRepository`` is unreachable from the running application, and stays that way.

    This is stronger than "``mark_delivered`` is never called". The repository is never
    *constructed*
    outside a test fixture: ``exchange/api.py``'s router factory builds
    ``LabResultReleaseRepository``, ``DoctorReviewRepository``, ``LabResultAmendmentRepository`` and
    every other repository the application needs, and deliberately does not build this one. So no
    endpoint can read or advance ``ResultDelivery``, and the only production code that touches the
    table at all is the ``INSERT`` in ``exchange/orders.py`` (see the next test for why that stays).

    If this test fails, someone wired the deprecated concept back into the application. Read ADR-064
    before making it pass -- the fix is almost certainly to use the canonical owner for whichever
    fact was intended, not to delete this assertion.
    """
    offenders: list[str] = []
    for path in _production_sources():
        if path == _DEFINING_MODULE:
            continue
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), 1):
            if "ResultDeliveryRepository" in line and not line.lstrip().startswith("#"):
                offenders.append(f"{path.relative_to(_SRC)}:{line_number}: {line.strip()}")

    assert offenders == [], (
        "ADR-064: no production module may construct, import or call "
        "ResultDeliveryRepository -- it is deprecated and compatibility-only. Offenders:\n  "
        + "\n  ".join(offenders)
    )


def test_rd6b_the_defining_module_is_the_only_place_the_repository_lives() -> None:
    """A companion to the test above, so its exclusion cannot silently become a loophole.

    If ``exchange/results.py`` stops defining the repository (because a future ticket removes it),
    this test fails and tells the next agent to delete the exclusion rather than leave a guard
    pointing at nothing. A guard that passes because its subject vanished is worse than no guard.
    """
    text = _DEFINING_MODULE.read_text(encoding="utf-8")
    assert "class ResultDeliveryRepository" in text, (
        "ResultDeliveryRepository no longer lives in exchange/results.py. If it was removed, "
        "delete _DEFINING_MODULE's exclusion and this test with it (ADR-064 phase C)."
    )


def test_rd6c_only_order_creation_writes_the_table() -> None:
    """``exchange/orders.py`` is the single production writer, and that is deliberate.

    ADR-064 keeps the row rather than stopping creation, for a reason worth stating where a future
    agent will read it: three qualified tests use the ``ResultDelivery`` insert as their proof that
    order creation commits as **one** aggregate --
    ``test_exchange_idempotency_concurrency.py``'s "no second row from the losing attempt",
    ``integration/test_dhex001r_postgres_qualification.py``'s nine-loser concurrency proof, and
    ``test_exchange_orders.py``'s rollback test, which patches ``ResultDelivery`` itself as the
    failure-injection point. Stopping creation would mean rewriting an atomicity proof to remove
    its injection site, which is churn against qualified tests for a row nothing reads.

    So the row stays, inert. What must not happen is a *second* writer appearing.
    """
    writers: list[str] = []
    for path in _production_sources():
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("#", "``", '"')):
                continue
            # `class ResultDelivery(Base)` is the declaration, not a write. Excluded explicitly
            # rather than by a cleverer pattern, because the first draft of this test counted it and
            # failed -- and a guard whose own matcher is subtle is a guard nobody will trust.
            if stripped.startswith("class "):
                continue
            # A constructor call, i.e. a write. `ResultDelivery(` with an open paren.
            if "ResultDelivery(" in line and "ResultDeliveryRecord(" not in line:
                writers.append(f"{path.relative_to(_SRC)}:{line_number}")

    assert writers and all(w.startswith("exchange/orders.py:") for w in writers), (
        "ADR-064: exchange/orders.py is the only production writer of ResultDelivery. "
        f"Found: {writers}"
    )


# -- why it is superseded rather than merely unused ----------------------------------------------


def test_rd5_the_superseding_model_is_version_scoped_and_this_one_is_not() -> None:
    """The structural reason ``ResultDelivery`` cannot own "the provider's system received it".

    This is the argument that makes ADR-064 a domain decision rather than dead-code tidying.

    ``ResultDelivery.lab_order_id`` is **unique** -- one row per order, one status. But since
    ADR-048
    a result is released as an immutable *version*, and an amendment produces another. So the real
    fact is necessarily version-scoped: version 1 can be delivered while version 2 is still owed.
    A single order-level status cannot express that state at all -- not "does not currently", but
    *cannot*, because the schema permits exactly one row.

    ``ConnectorDeliveryObligation`` is unique on ``release_version_id`` instead, which is the same
    fact modelled at the granularity the fact actually has.

    If someone ever tries to rescue ``ResultDelivery`` by making it version-scoped, this test fails
    and points them at ADR-064: the answer is to use the model that already does it.
    """
    order_scoped = {c.name for c in ResultDelivery.__table__.columns if c.unique}
    assert order_scoped == {"lab_order_id"}, (
        "ResultDelivery is expected to be order-scoped (one row per order). If this changed, "
        "someone is reviving a deprecated concept -- read ADR-064."
    )

    obligation_unique = {
        tuple(sorted(col.name for col in constraint.columns))
        for constraint in ConnectorDeliveryObligation.__table__.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("release_version_id",) in obligation_unique, (
        "ConnectorDeliveryObligation is expected to be scoped to one release version -- that is "
        "why it, and not ResultDelivery, owns external provider-system delivery (ADR-064)."
    )

    # And the thing both are about is itself a version, not an order.
    assert "lab_order_id" in LabResultReleaseVersion.__table__.columns
    assert not LabResultReleaseVersion.__table__.columns["lab_order_id"].unique, (
        "A release version must not be unique per order -- amendments create further versions, "
        "which is the whole reason order-level delivery status is the wrong shape (ADR-048/064)."
    )


def test_rd_status_enum_is_untouched_so_historical_rows_stay_readable() -> None:
    """Deprecation is not removal. Historical rows keep their meaning and remain readable.

    ADR-064 phase A preserves the table, the enum and every existing row. Nothing in this ticket
    migrates, rewrites or deletes data, and the CHECK constraint that makes old rows legible stays.
    """
    from daher_adapter.exchange.models import ResultDeliveryStatus

    assert {s.value for s in ResultDeliveryStatus} == {"PENDING", "DELIVERED", "FAILED"}
    checks = {
        c.name
        for c in ResultDelivery.__table__.constraints
        if c.__class__.__name__ == "CheckConstraint"
    }
    assert "ck_exchange_result_deliveries_status" in checks


@pytest.mark.parametrize(
    ("fact", "module_path", "owner"),
    [
        (
            "a released clinical result exists",
            "daher_adapter.exchange.models",
            "LabResultReleaseVersion",
        ),
        ("a clinician reviewed one exact version", "daher_adapter.exchange.models", "DoctorReview"),
        (
            "a provider's own system received it",
            "daher_adapter.connect.runtime_models",
            "ConnectorDeliveryObligation",
        ),
        ("the patient was messaged", "daher_adapter.notify.models", "PatientNotification"),
    ],
)
def test_rd_each_canonical_owner_still_exists(fact: str, module_path: str, owner: str) -> None:
    """ADR-064's ownership table names four models. If one is renamed or removed, the ADR is stale.

    The **module** is part of the assertion, not incidental. These four facts live in three
    different
    packages -- ``exchange``, ``connect`` and ``notify`` -- and that separation is itself the point:
    patient messaging and external-system delivery are not the Exchange's clinical concern, and
    keeping them in their own packages is what stops the vocabulary collapsing back into one word.
    The first draft of this test looked for all four in ``exchange.models`` and failed, which is the
    separation announcing itself.
    """
    import importlib

    module = importlib.import_module(module_path)
    assert hasattr(module, owner), (
        f"ADR-064 assigns {fact!r} to {module_path}.{owner}, which no longer exists"
    )
