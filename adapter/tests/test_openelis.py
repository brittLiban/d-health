from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest

from daher_adapter.openelis import (
    HttpResult,
    MatchCount,
    OpenElisClient,
    OpenElisProtocolError,
    ServiceRequestDeliveryBlocked,
    VerificationOutcome,
)
from daher_adapter.recovery import SourceCorrelation, TargetPresence

PATIENT_PAYLOAD = {"resourceType": "Patient"}
PRACTITIONER_PAYLOAD = {"resourceType": "Practitioner"}


def bundle(*resources: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        "resourceType": "Bundle",
        "entry": [{"resource": r} for r in resources],
    }


def resource(resource_type: str, fhir_id: str | None = None) -> Mapping[str, Any]:
    body: dict[str, Any] = {"resourceType": resource_type}
    if fhir_id is not None:
        body["id"] = fhir_id
    return body


def run(coro):
    return asyncio.run(coro)


class ScriptedTransport:
    """Records every call and returns pre-scripted responses in order, per verb."""

    def __init__(self) -> None:
        self.gets: list[str] = []
        self.posts: list[tuple[str, Mapping[str, Any]]] = []
        self._get_responses: list[Any] = []
        self._post_responses: list[Any] = []

    def queue_get(self, response) -> None:
        self._get_responses.append(response)

    def queue_post(self, response) -> None:
        self._post_responses.append(response)

    async def get(self, url: str):
        self.gets.append(url)
        response = self._get_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def post(self, url: str, payload: Mapping[str, Any]):
        self.posts.append((url, payload))
        response = self._post_responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def client(transport: ScriptedTransport) -> OpenElisClient:
    base = "https://oe.openelis.org:8443/OpenELIS-Global/fhir"
    return OpenElisClient(base, transport.get, transport.post)


def create_patient(transport: ScriptedTransport):
    return run(client(transport).create_patient(PATIENT_PAYLOAD, system="urn:x", value="v1"))


def correlation(value: str = "deadbeef") -> SourceCorrelation:
    return SourceCorrelation(system="urn:daher-health:adapter:source-event", value=value)


# --- identifier_search -------------------------------------------------------------


def test_identifier_search_zero_matches() -> None:
    transport = ScriptedTransport()
    transport.queue_get(HttpResult(status=200, body=bundle()))

    result = run(client(transport).identifier_search("Practitioner", "urn:x", "nope"))

    assert result.count is MatchCount.ZERO
    assert result.fhir_id is None
    assert "identifier=urn%3Ax%7Cnope" in transport.gets[0]


def test_identifier_search_exact_one_match_returns_fhir_id() -> None:
    transport = ScriptedTransport()
    transport.queue_get(HttpResult(status=200, body=bundle(resource("Practitioner", "abc-123"))))

    result = run(client(transport).identifier_search("Practitioner", "urn:x", "value1"))

    assert result.count is MatchCount.ONE
    assert result.fhir_id == "abc-123"


def test_identifier_search_many_matches_is_ambiguous_not_the_first_id() -> None:
    transport = ScriptedTransport()
    two = bundle(resource("Practitioner", "one"), resource("Practitioner", "two"))
    transport.queue_get(HttpResult(status=200, body=two))

    result = run(client(transport).identifier_search("Practitioner", "urn:x", "value1"))

    assert result.count is MatchCount.MANY
    assert result.fhir_id is None


def test_identifier_search_non_200_is_unreachable_not_zero() -> None:
    transport = ScriptedTransport()
    transport.queue_get(HttpResult(status=400, body=resource("OperationOutcome")))

    result = run(client(transport).identifier_search("ServiceRequest", "urn:x", "value1"))

    # A rejected query (e.g. against the unpatched image, or a transient 5xx) must never be
    # read as "proven absent" -- that is exactly the false-ABSENT DH-025 forbids.
    assert result.count is MatchCount.UNREACHABLE


def test_identifier_search_transport_exception_is_unreachable() -> None:
    transport = ScriptedTransport()
    transport.queue_get(ConnectionError("refused"))

    result = run(client(transport).identifier_search("Patient", "urn:x", "value1"))

    assert result.count is MatchCount.UNREACHABLE


def test_identifier_search_timeout_is_unreachable_not_absence() -> None:
    """A timeout proves nothing about whether the target has a match -- treating it as ZERO
    would let DH-025 retry-create against a target it never actually reached."""
    transport = ScriptedTransport()
    transport.queue_get(TimeoutError("timed out"))

    result = run(client(transport).identifier_search("ServiceRequest", "urn:x", "value1"))

    assert result.count is MatchCount.UNREACHABLE


def test_identifier_search_401_is_unreachable_not_absence() -> None:
    transport = ScriptedTransport()
    transport.queue_get(HttpResult(status=401, body=None))

    result = run(client(transport).identifier_search("ServiceRequest", "urn:x", "value1"))

    assert result.count is MatchCount.UNREACHABLE


def test_identifier_search_403_is_unreachable_not_absence() -> None:
    transport = ScriptedTransport()
    transport.queue_get(HttpResult(status=403, body=None))

    result = run(client(transport).identifier_search("ServiceRequest", "urn:x", "value1"))

    assert result.count is MatchCount.UNREACHABLE


def test_identifier_search_matched_entry_without_id_is_unreachable_not_fabricated() -> None:
    transport = ScriptedTransport()
    transport.queue_get(HttpResult(status=200, body=bundle(resource("Practitioner"))))

    result = run(client(transport).identifier_search("Practitioner", "urn:x", "value1"))

    assert result.count is MatchCount.UNREACHABLE
    assert result.fhir_id is None


def test_non_bundle_response_raises_protocol_error() -> None:
    transport = ScriptedTransport()
    transport.queue_get(HttpResult(status=200, body=resource("OperationOutcome")))

    with pytest.raises(OpenElisProtocolError):
        run(client(transport).identifier_search("Patient", "urn:x", "value1"))


# --- create + verify-after-write (PatientProvider false-success defense, §8) -------


def test_create_patient_201_with_zero_readback_is_failed_not_success() -> None:
    """Reproduces known bug 19: POST returns 201 but the native persist did not happen."""
    transport = ScriptedTransport()
    transport.queue_post(HttpResult(status=201, body=resource("OperationOutcome")))
    transport.queue_get(HttpResult(status=200, body=bundle()))  # read-back proves zero

    result = create_patient(transport)

    assert result.outcome is VerificationOutcome.FAILED_NOT_PERSISTED
    assert result.fhir_id is None


def test_create_patient_201_with_exactly_one_readback_is_success() -> None:
    transport = ScriptedTransport()
    transport.queue_post(HttpResult(status=201, body=resource("OperationOutcome")))
    transport.queue_get(HttpResult(status=200, body=bundle(resource("Patient", "p-1"))))

    result = create_patient(transport)

    assert result.outcome is VerificationOutcome.SUCCESS
    assert result.fhir_id == "p-1"


def test_create_patient_two_readback_matches_is_ambiguous() -> None:
    transport = ScriptedTransport()
    transport.queue_post(HttpResult(status=201, body=resource("OperationOutcome")))
    two = bundle(resource("Patient", "p-1"), resource("Patient", "p-2"))
    transport.queue_get(HttpResult(status=200, body=two))

    result = create_patient(transport)

    assert result.outcome is VerificationOutcome.AMBIGUOUS
    assert result.fhir_id is None


def test_create_patient_readback_unavailable_is_ambiguous_never_success() -> None:
    transport = ScriptedTransport()
    transport.queue_post(HttpResult(status=201, body=resource("OperationOutcome")))
    transport.queue_get(HttpResult(status=503, body=None))

    result = create_patient(transport)

    assert result.outcome is VerificationOutcome.LOOKUP_UNAVAILABLE


def test_create_patient_post_never_reaching_server_is_lookup_unavailable() -> None:
    transport = ScriptedTransport()
    transport.queue_post(TimeoutError("no route"))

    result = create_patient(transport)

    assert result.outcome is VerificationOutcome.LOOKUP_UNAVAILABLE
    assert len(transport.gets) == 0  # never attempted a read-back for a POST that never landed


def test_create_conflict_response_reconciles_by_search_never_blind_retries() -> None:
    """A duplicate-create conflict (e.g. the DB-level uniqueness patch rejecting a race loser)
    must never be trusted or retried from its own status code -- read-back-only verification
    already handles this generically: the POST's own status is advisory, so a losing writer's
    conflict response and a winning writer's success response are indistinguishable here on
    purpose, and both resolve to whatever the exact search actually finds."""
    transport = ScriptedTransport()
    # A non-2xx "conflict" response carrying no created resource -- exactly the shape a
    # duplicate-external_id constraint violation would produce.
    transport.queue_post(HttpResult(status=500, body=resource("OperationOutcome")))
    # The read-back finds the winning writer's row -- not a second create attempt.
    transport.queue_get(HttpResult(status=200, body=bundle(resource("Patient", "winner-id"))))

    result = create_patient(transport)

    assert result.outcome is VerificationOutcome.SUCCESS
    assert result.fhir_id == "winner-id"
    assert len(transport.posts) == 1  # exactly the one attempt -- no retry was issued


def test_create_practitioner_success_uses_practitioner_resource_type() -> None:
    transport = ScriptedTransport()
    transport.queue_post(HttpResult(status=201, body=resource("OperationOutcome")))
    transport.queue_get(HttpResult(status=200, body=bundle(resource("Practitioner", "pr-1"))))

    practitioner = client(transport)
    result = run(practitioner.create_practitioner(PRACTITIONER_PAYLOAD, system="urn:x", value="v1"))

    assert result.outcome is VerificationOutcome.SUCCESS
    assert result.fhir_id == "pr-1"
    assert transport.posts[0][0].endswith("/Practitioner")
    assert "Practitioner?identifier=" in transport.gets[0]


# --- DH-025 target probe binding ----------------------------------------------------


def test_service_request_probe_zero_matches_is_absent() -> None:
    transport = ScriptedTransport()
    transport.queue_get(HttpResult(status=200, body=bundle()))

    result = run(client(transport).service_request_probe(correlation()))

    assert result.presence is TargetPresence.ABSENT
    assert result.record is None


def test_service_request_probe_one_match_is_found_with_record() -> None:
    transport = ScriptedTransport()
    transport.queue_get(HttpResult(status=200, body=bundle(resource("ServiceRequest", "sr-1"))))

    result = run(client(transport).service_request_probe(correlation()))

    assert result.presence is TargetPresence.FOUND
    assert result.record is not None
    assert result.record.system == "ServiceRequest"
    assert result.record.record_id == "sr-1"


def test_service_request_probe_many_matches_is_ambiguous() -> None:
    transport = ScriptedTransport()
    two = bundle(resource("ServiceRequest", "sr-1"), resource("ServiceRequest", "sr-2"))
    transport.queue_get(HttpResult(status=200, body=two))

    result = run(client(transport).service_request_probe(correlation()))

    assert result.presence is TargetPresence.AMBIGUOUS


def test_service_request_probe_unreachable_target_is_ambiguous_never_absent() -> None:
    """The exact failure mode this whole file exists to prevent: an unreachable target must
    never be read as proof of absence, or DH-025 would retry-create against a target it never
    actually checked."""
    transport = ScriptedTransport()
    transport.queue_get(HttpResult(status=400, body=resource("OperationOutcome")))

    result = run(client(transport).service_request_probe(correlation()))

    assert result.presence is TargetPresence.AMBIGUOUS


def test_service_request_probe_against_unpatched_server_is_ambiguous_not_absent() -> None:
    """Before DH-011D's search-parameter patch, ServiceRequest?identifier= is rejected with
    400 on every call -- this must never be misread as zero matches."""
    transport = ScriptedTransport()
    transport.queue_get(HttpResult(status=400, body=resource("OperationOutcome")))

    result = run(client(transport).service_request_probe(correlation("any-value")))

    assert result.presence is TargetPresence.AMBIGUOUS


# --- delivery gate --------------------------------------------------------------


def test_deliver_service_request_is_not_activated() -> None:
    transport = ScriptedTransport()
    payload = {"resourceType": "ServiceRequest"}

    with pytest.raises(ServiceRequestDeliveryBlocked):
        run(client(transport).deliver_service_request(correlation(), payload))

    assert len(transport.posts) == 0  # never attempted -- refuses before any network call
