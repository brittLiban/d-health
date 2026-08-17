"""Cohort assembly: dedupe, prove completeness, order locally.

Every test here corresponds to something measured on the running stack, not imagined:
`_sort` is ignored, whole-second collisions are routine, and the inclusive lower bound
re-delivers the boundary second every cycle.
"""

from __future__ import annotations

import pytest

from daher_adapter.source_events import (
    CohortIncomplete,
    ResourceRejected,
    assemble_cohort,
    event_from_resource,
)


def sr(rid: str, updated: str, *, version: str = "1", status: str = "active", replaces=None):
    resource: dict = {
        "resourceType": "ServiceRequest",
        "id": rid,
        "meta": {"versionId": version, "lastUpdated": updated},
        "status": status,
        "intent": "order",
    }
    if replaces:
        resource["replaces"] = [{"reference": r, "type": "ServiceRequest"} for r in replaces]
    return resource


T1 = "2026-08-11T10:24:58.000+03:00"
T2 = "2026-08-11T10:24:59.000+03:00"
T3 = "2026-08-11T10:25:00.000+03:00"


class TestOrderingIsOursNotTheServers:
    def test_an_unordered_response_is_sorted(self):
        """The server returns byte-identical order for ascending, descending and nonsense sorts."""
        cohort = assemble_cohort(
            [sr("c", T3), sr("a", T1), sr("b", T2)],
            reported_total=3,
            resource_type="ServiceRequest",
        )
        assert [e.resource_id for e in cohort.events] == ["a", "b", "c"]

    def test_a_reversed_response_is_sorted_identically(self):
        forward = assemble_cohort(
            [sr("a", T1), sr("b", T2), sr("c", T3)],
            reported_total=3,
            resource_type="ServiceRequest",
        )
        backward = assemble_cohort(
            [sr("c", T3), sr("b", T2), sr("a", T1)],
            reported_total=3,
            resource_type="ServiceRequest",
        )
        assert [e.resource_id for e in forward.events] == [e.resource_id for e in backward.events]

    def test_identical_timestamps_get_a_deterministic_tie_break(self):
        """8 real records share one second here. Without a tie-break, 'last contiguous success'
        is not reproducible between two cycles over the same data."""
        first = assemble_cohort(
            [sr("z", T1), sr("m", T1), sr("a", T1)],
            reported_total=3,
            resource_type="ServiceRequest",
        )
        second = assemble_cohort(
            [sr("m", T1), sr("a", T1), sr("z", T1)],
            reported_total=3,
            resource_type="ServiceRequest",
        )
        assert [e.resource_id for e in first.events] == ["a", "m", "z"]
        assert [e.resource_id for e in second.events] == ["a", "m", "z"]

    def test_the_highest_instant_is_the_last_event(self):
        cohort = assemble_cohort(
            [sr("c", T3), sr("a", T1)], reported_total=2, resource_type="ServiceRequest"
        )
        assert cohort.highest.raw == T3


class TestDuplicatesAreAbsorbed:
    def test_the_same_resource_version_twice_is_one_event(self):
        """Normal operation: `ge` re-delivers the boundary second every cycle (§4)."""
        cohort = assemble_cohort(
            [sr("a", T1), sr("a", T1)], reported_total=1, resource_type="ServiceRequest"
        )
        assert len(cohort.events) == 1
        assert cohort.duplicates_absorbed == 1

    def test_two_versions_of_one_resource_are_two_events(self):
        """A corrected resource must not be swallowed as a duplicate of its earlier version."""
        cohort = assemble_cohort(
            [sr("a", T1, version="1"), sr("a", T2, version="2")],
            reported_total=2,
            resource_type="ServiceRequest",
        )
        assert len(cohort.events) == 2

    def test_duplicates_do_not_make_a_window_look_oversubscribed(self):
        cohort = assemble_cohort(
            [sr("a", T1), sr("a", T1), sr("a", T1)],
            reported_total=1,
            resource_type="ServiceRequest",
        )
        assert len(cohort.events) == 1


class TestCompletenessIsProvenNotAssumed:
    def test_a_truncated_window_is_refused(self):
        """The serious one. An unordered server plus offset paging can omit a record silently."""
        with pytest.raises(CohortIncomplete) as raised:
            assemble_cohort([sr("a", T1)], reported_total=5, resource_type="ServiceRequest")
        assert "truncated" in str(raised.value)

    def test_receiving_more_than_reported_is_allowed(self):
        """`total` can lag a concurrent write; a surplus cannot hide a skipped record."""
        cohort = assemble_cohort(
            [sr("a", T1), sr("b", T2)], reported_total=1, resource_type="ServiceRequest"
        )
        assert len(cohort.events) == 2

    def test_an_empty_window_is_normal(self):
        cohort = assemble_cohort([], reported_total=0, resource_type="ServiceRequest")
        assert cohort.events == () and cohort.highest is None


class TestCancellationMustSurvive:
    def test_a_discontinuation_carrying_replaces_is_kept(self):
        """§6: the original order's `meta.lastUpdated` never advances when it is stopped, so this
        new resource is the ONLY way the cancellation is ever visible to a watermark."""
        event = event_from_resource(
            sr("new", T2, status="unknown", replaces=["ServiceRequest/original"]),
            resource_type="ServiceRequest",
        )
        assert event.replaces == ("ServiceRequest/original",)
        assert event.status == "unknown"

    def test_status_unknown_is_not_filtered_out(self):
        """A rule like 'only handle active orders' would drop every cancellation."""
        cohort = assemble_cohort(
            [sr("a", T1, status="unknown"), sr("b", T2, status="revoked")],
            reported_total=2,
            resource_type="ServiceRequest",
        )
        assert len(cohort.events) == 2

    def test_a_malformed_replaces_does_not_crash_assembly(self):
        resource = sr("a", T1)
        resource["replaces"] = ["not-an-object", {"no_reference": True}]
        event = event_from_resource(resource, resource_type="ServiceRequest")
        assert event.replaces == ()


class TestResourcesThatCannotBeProcessedSafely:
    def test_a_resource_without_an_id_is_refused(self):
        resource = sr("a", T1)
        del resource["id"]
        with pytest.raises(ResourceRejected):
            event_from_resource(resource, resource_type="ServiceRequest")

    def test_a_resource_without_a_version_is_refused(self):
        """The ledger identity is (type, id, versionId); without it corrections collide."""
        resource = sr("a", T1)
        del resource["meta"]["versionId"]
        with pytest.raises(ResourceRejected) as raised:
            event_from_resource(resource, resource_type="ServiceRequest")
        assert "versionId" in str(raised.value)

    def test_a_resource_with_a_malformed_timestamp_is_refused(self):
        with pytest.raises(ResourceRejected):
            event_from_resource(sr("a", "yesterday"), resource_type="ServiceRequest")

    def test_a_resource_with_no_timestamp_is_refused(self):
        resource = sr("a", T1)
        del resource["meta"]["lastUpdated"]
        with pytest.raises(ResourceRejected):
            event_from_resource(resource, resource_type="ServiceRequest")

    def test_the_wrong_resource_type_is_refused(self):
        with pytest.raises(ResourceRejected):
            event_from_resource(
                {
                    "resourceType": "Patient",
                    "id": "p",
                    "meta": {"versionId": "1", "lastUpdated": T1},
                },
                resource_type="ServiceRequest",
            )

    def test_one_bad_resource_fails_the_whole_cohort(self):
        """Not skipped: a resource we cannot order is a record we cannot prove we handled."""
        with pytest.raises(ResourceRejected):
            assemble_cohort(
                [sr("a", T1), sr("b", "nonsense")],
                reported_total=2,
                resource_type="ServiceRequest",
            )
