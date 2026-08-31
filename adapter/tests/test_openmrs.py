from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from daher_adapter.ledger import MessageIdentity
from daher_adapter.openmrs import (
    OpenMrsOrderSource,
    OpenMrsProtocolError,
    OpenMrsResourceNotFound,
    SourcePresence,
    check_source_supersession,
)


def identity(*, version: str = "1") -> MessageIdentity:
    return MessageIdentity(
        source_system="openmrs",
        direction="openmrs_to_openelis",
        resource_type="ServiceRequest",
        resource_id="order-uuid",
        resource_version=version,
    )


def sr(*, version: str = "1", status: str = "active", rid: str = "order-uuid") -> dict[str, Any]:
    return {
        "resourceType": "ServiceRequest",
        "id": rid,
        "meta": {"versionId": version, "lastUpdated": "2026-08-30T10:00:00.000+03:00"},
        "status": status,
    }


class ScriptedFetch:
    """The injected `_fetch` callable -- a resource, `OpenMrsResourceNotFound`, or a plain
    transport exception, exactly like the real `_fetch` implementation must be able to raise."""

    def __init__(self, response: Mapping[str, Any] | Exception) -> None:
        self._response = response
        self.urls: list[str] = []

    def __call__(self, url: str) -> Mapping[str, Any]:
        self.urls.append(url)
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def source(response: Mapping[str, Any] | Exception) -> tuple[OpenMrsOrderSource, ScriptedFetch]:
    fetch = ScriptedFetch(response)
    return OpenMrsOrderSource("http://openmrs/fhir", fetch), fetch


def test_read_resource_returns_the_resource_for_a_matching_url() -> None:
    src, fetch = source(sr())

    result = src.read_resource("ServiceRequest", "order-uuid")

    assert result == sr()
    assert fetch.urls == ["http://openmrs/fhir/ServiceRequest/order-uuid"]


def test_read_resource_url_encodes_the_resource_id() -> None:
    src, fetch = source(sr(rid="a/b"))

    src.read_resource("ServiceRequest", "a/b")

    assert fetch.urls == ["http://openmrs/fhir/ServiceRequest/a%2Fb"]


def test_read_resource_returns_none_on_confirmed_not_found() -> None:
    src, _ = source(OpenMrsResourceNotFound("404"))

    assert src.read_resource("ServiceRequest", "order-uuid") is None


def test_read_resource_raises_on_wrong_resource_type_returned() -> None:
    src, _ = source({"resourceType": "OperationOutcome"})

    with pytest.raises(OpenMrsProtocolError, match="ServiceRequest"):
        src.read_resource("ServiceRequest", "order-uuid")


def test_supersession_current_when_identity_and_status_unchanged() -> None:
    src, _ = source(sr(version="1", status="active"))

    result = check_source_supersession(identity(version="1"), {"status": "active"}, source=src)

    assert result.presence == SourcePresence.CURRENT


def test_supersession_superseded_on_version_drift() -> None:
    """The documented, tested OpenMRS signal (WATERMARK-DESIGN.md §6) is a status change without
    a `lastUpdated`/`versionId` guarantee either way -- this proves the identity comparison alone
    also catches drift, independent of whether status happened to change too."""
    src, _ = source(sr(version="2", status="active"))

    result = check_source_supersession(identity(version="1"), {"status": "active"}, source=src)

    assert result.presence == SourcePresence.SUPERSEDED
    assert "version" in result.detail


def test_supersession_superseded_on_status_change_with_version_unchanged() -> None:
    """DH-011E4B §3/§4: the complete OpenMRS/openmrs-core 2.8.7 order-action matrix, source-proven
    (`OrderServiceImpl.saveOrderInternal`/`stopOrder`, fhir2 4.2.0's
    `ServiceRequestTranslatorImpl.determineServiceRequestStatus`) and live-confirmed against a real
    disposable stack for both actions that can supersede an existing order:

    NEW: not a successor action (this creates the original itself); n/a throughout.

    REVISE: a successor order is always created; `previousOrder` is required (throws
    `MissingRequiredPropertyException` if absent); the original IS mutated -- `stopOrder` sets its
    `dateStopped`; its FHIR status afterward is `completed`; the preflight detects this: YES.

    DISCONTINUE: a successor order is always created; `previousOrder` is set explicitly or
    auto-matched by concept; the original IS mutated -- `stopOrder` sets its `dateStopped`; its
    FHIR status afterward is `completed` (the discontinuing order itself shows unknown/revoked);
    the preflight detects this: YES.

    RENEW: not a real successor relationship in this version -- `previousOrder` may be set but is
    unenforced; the original is NOT mutated (`Action.RENEW` is never checked in
    `saveOrderInternal`'s branching; zero references to it anywhere in `openmrs-core` outside the
    enum declaration, zero test coverage); the original's status is unaffected; the preflight has
    nothing to detect, because RENEW cannot supersede anything under this version's actual
    implementation.

    **The invariant this proves**: `OrderServiceImpl.saveOrderInternal` routes both `REVISE` and
    `DISCONTINUE` through the identical `stopOrder` method on the previous/original order
    (`stopOrder` itself refuses -- `CannotStopInactiveOrderException` -- unless that order is
    currently active, so the transition always either succeeds cleanly from active or the whole
    save fails outright; there is no third outcome). `determineServiceRequestStatus` derives the
    original's FHIR status purely from `dateStopped`/`autoExpireDate`, never from `Action` --
    so *every* V1 order action capable of superseding an existing order necessarily changes that
    order's own FHIR status into something this preflight refuses. `RENEW` is not a counter-example
    because it cannot supersede anything under this openmrs-core version's actual implementation.

    Live-confirmed on a disposable OpenMRS-only stack (2026-08-31), both actions: `status` flips
    (`active` -> `completed`) while `meta.lastUpdated`/`meta.versionId` do not move at all
    (WATERMARK-DESIGN.md §6's 2026-08-13 finding, reconfirmed for DISCONTINUE and newly confirmed
    for REVISE). The real, unmodified `check_source_supersession` function was run against both
    real scenarios and classified both `SUPERSEDED`. This unit test exercises the same status-only
    signal synchronously; the live proof is recorded in PROVENANCE.md's DH-011E4B section, not
    re-run on every CI pass."""
    src, _ = source(sr(version="1", status="completed"))

    result = check_source_supersession(identity(version="1"), {"status": "active"}, source=src)

    assert result.presence == SourcePresence.SUPERSEDED
    assert "status" in result.detail


def test_supersession_superseded_after_revise_is_indistinguishable_from_discontinue() -> None:
    """REVISE and DISCONTINUE both flip the original's status the identical way (see the matrix
    above) -- the preflight has no `Action`-specific branch and does not need one; this test
    exists so a future reader does not have to re-derive that from source to trust it."""
    src, _ = source(sr(version="1", status="completed"))

    result = check_source_supersession(identity(version="1"), {"status": "active"}, source=src)

    assert result.presence == SourcePresence.SUPERSEDED


def test_supersession_ambiguous_when_resource_no_longer_exists() -> None:
    src, _ = source(OpenMrsResourceNotFound("404"))

    result = check_source_supersession(identity(), {"status": "active"}, source=src)

    assert result.presence == SourcePresence.AMBIGUOUS


def test_supersession_ambiguous_when_resource_unparseable() -> None:
    src, _ = source({"resourceType": "ServiceRequest", "id": "order-uuid"})  # no meta.versionId

    result = check_source_supersession(identity(), {"status": "active"}, source=src)

    assert result.presence == SourcePresence.AMBIGUOUS


def test_supersession_unreachable_on_transport_failure() -> None:
    src, _ = source(ConnectionError("connection refused"))

    result = check_source_supersession(identity(), {"status": "active"}, source=src)

    assert result.presence == SourcePresence.UNREACHABLE


def test_supersession_detail_never_carries_the_raw_payload() -> None:
    """§11/§17: the operator-facing `detail` string must stay short and non-PHI even when the
    underlying resource carries a patient reference."""
    resource = sr(version="2")
    resource["subject"] = {"reference": "Patient/12345", "display": "Jane Doe"}
    src, _ = source(resource)

    result = check_source_supersession(identity(version="1"), {"status": "active"}, source=src)

    assert "Patient" not in result.detail
    assert "Jane" not in result.detail
