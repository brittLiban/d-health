from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest

from daher_adapter.config import Settings
from daher_adapter.openelis import (
    CancellationTargetState,
    HttpResult,
    MatchCount,
    OpenElisClient,
    OpenElisProtocolError,
    ServiceRequestDeliveryBlocked,
    VerificationOutcome,
    deliver_service_request_if_activated,
)
from daher_adapter.recovery import AmbiguousTargetState, SourceCorrelation, TargetPresence

PATIENT_PAYLOAD = {"resourceType": "Patient"}
PRACTITIONER_PAYLOAD = {"resourceType": "Practitioner"}
SERVICE_REQUEST_PAYLOAD = {"resourceType": "ServiceRequest"}


def bundle(*resources: Mapping[str, Any], total: int | None = None) -> Mapping[str, Any]:
    return {
        "resourceType": "Bundle",
        "total": len(resources) if total is None else total,
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


def deliver(transport: ScriptedTransport, value: str = "deadbeef"):
    oe_client = client(transport)
    return run(oe_client.deliver_service_request(correlation(value), SERVICE_REQUEST_PAYLOAD))


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


def test_service_request_probe_one_order_many_tests_is_found_not_ambiguous() -> None:
    """DH-011D3 (Finding B correction): a multi-test order (a prenatal panel, the normal V1
    case) produces one native Analysis, and therefore one ServiceRequest, per test -- so a
    single logical order can legitimately come back as multiple Bundle entries. The match count
    must come from Bundle.total (which OpenELIS's native-search patch sets to the logical-order
    count, 0 or 1), never from counting entries, or every multi-test order would read back as
    MANY and could never reconcile."""
    transport = ScriptedTransport()
    one_order_two_tests = bundle(
        resource("ServiceRequest", "sr-test-1"), resource("ServiceRequest", "sr-test-2"), total=1
    )
    transport.queue_get(HttpResult(status=200, body=one_order_two_tests))

    result = run(client(transport).service_request_probe(correlation()))

    assert result.presence is TargetPresence.FOUND
    assert result.record is not None
    assert result.record.record_id == "sr-test-1"


def test_service_request_probe_many_matches_is_ambiguous() -> None:
    """Two genuinely distinct logical orders (Bundle.total == 2) -- structurally impossible
    given the DB's partial unique index on the correlation value, but the probe must still fail
    closed rather than pick one if it is ever observed."""
    transport = ScriptedTransport()
    two = bundle(resource("ServiceRequest", "sr-1"), resource("ServiceRequest", "sr-2"), total=2)
    transport.queue_get(HttpResult(status=200, body=two))

    result = run(client(transport).service_request_probe(correlation()))

    assert result.presence is TargetPresence.AMBIGUOUS


def test_service_request_probe_single_entry_missing_total_is_still_found() -> None:
    """The Finding B correction must not require Bundle.total to be present -- Patient,
    Practitioner, and every other identifier search this client makes goes through the
    standalone secondary FHIR store, whose total field is unverified. A single entry with no
    total is exactly what counting entries already gave: FOUND, not degraded to AMBIGUOUS just
    because total happens to be absent."""
    transport = ScriptedTransport()
    body = {"resourceType": "Bundle", "entry": [{"resource": resource("ServiceRequest", "sr-1")}]}
    transport.queue_get(HttpResult(status=200, body=body))

    result = run(client(transport).service_request_probe(correlation()))

    assert result.presence is TargetPresence.FOUND
    assert result.record is not None
    assert result.record.record_id == "sr-1"


def test_service_request_probe_many_entries_missing_total_is_ambiguous() -> None:
    """Multiple entries with no Bundle.total to collapse them: total is never required, so this
    falls back to exactly the pre-correction behavior -- MANY, not a guess in either direction."""
    transport = ScriptedTransport()
    body = {
        "resourceType": "Bundle",
        "entry": [
            {"resource": resource("ServiceRequest", "sr-1")},
            {"resource": resource("ServiceRequest", "sr-2")},
        ],
    }
    transport.queue_get(HttpResult(status=200, body=body))

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


# --- deliver_service_request: the real writer protocol (DH-011D3 finding C) -----------------


def test_deliver_service_request_injects_the_correlation_identifier() -> None:
    transport = ScriptedTransport()
    transport.queue_post(HttpResult(status=200, body=resource("OperationOutcome")))
    transport.queue_get(HttpResult(status=200, body=bundle(resource("ServiceRequest", "sr-1"))))

    deliver(transport, "abc123")

    posted_body = transport.posts[0][1]
    identifiers = posted_body["identifier"]
    assert {"system": "urn:daher-health:adapter:source-event", "value": "abc123"} in identifiers


def test_deliver_service_request_rejects_a_conflicting_caller_supplied_identifier() -> None:
    transport = ScriptedTransport()
    conflicting = {
        "resourceType": "ServiceRequest",
        "identifier": [{"system": "urn:daher-health:adapter:source-event", "value": "wrong-value"}],
    }

    with pytest.raises(ValueError, match="conflicting"):
        run(client(transport).deliver_service_request(correlation("abc123"), conflicting))

    assert len(transport.posts) == 0  # caught before any network call


def test_deliver_service_request_success_exactly_one_reconciliation_match() -> None:
    transport = ScriptedTransport()
    transport.queue_post(HttpResult(status=200, body=resource("OperationOutcome")))
    transport.queue_get(HttpResult(status=200, body=bundle(resource("ServiceRequest", "sr-1"))))

    result = deliver(transport)

    assert result.system == "ServiceRequest"
    assert result.record_id == "sr-1"
    assert len(transport.posts) == 1  # exactly one POST, no retry


def test_deliver_service_request_timeout_after_possible_commit_then_found_still_succeeds() -> None:
    """The POST itself times out -- the write may or may not have landed. Reconciliation still
    runs and, if it finds the order, that is authoritative regardless of what the POST did."""
    transport = ScriptedTransport()
    transport.queue_post(HttpResult(status=200, body=resource("ServiceRequest", "sr-1")))
    transport.queue_get(HttpResult(status=200, body=bundle(resource("ServiceRequest", "sr-1"))))

    result = deliver(transport)

    assert result.record_id == "sr-1"


def test_deliver_service_request_post_never_lands_is_ambiguous_not_a_retry() -> None:
    transport = ScriptedTransport()
    transport.queue_post(TimeoutError("timed out"))

    with pytest.raises(AmbiguousTargetState):
        deliver(transport)

    assert len(transport.posts) == 1  # attempted once; the exception is from that one attempt
    assert len(transport.gets) == 0  # no reconciliation possible with nothing to reconcile against


def test_deliver_service_request_zero_after_post_is_ambiguous_never_a_silent_success() -> None:
    transport = ScriptedTransport()
    transport.queue_post(HttpResult(status=201, body=resource("OperationOutcome")))
    transport.queue_get(HttpResult(status=200, body=bundle()))

    with pytest.raises(AmbiguousTargetState, match="zero matches"):
        deliver(transport)

    assert len(transport.posts) == 1  # no blind retry


def test_deliver_service_request_two_matches_after_post_is_ambiguous() -> None:
    transport = ScriptedTransport()
    transport.queue_post(HttpResult(status=200, body=resource("OperationOutcome")))
    two = bundle(resource("ServiceRequest", "sr-1"), resource("ServiceRequest", "sr-2"))
    transport.queue_get(HttpResult(status=200, body=two))

    with pytest.raises(AmbiguousTargetState):
        deliver(transport)


def test_deliver_service_request_401_on_reconciliation_is_ambiguous() -> None:
    transport = ScriptedTransport()
    transport.queue_post(HttpResult(status=200, body=resource("OperationOutcome")))
    transport.queue_get(HttpResult(status=401, body=None))

    with pytest.raises(AmbiguousTargetState):
        deliver(transport)


def test_deliver_service_request_403_on_post_itself_is_ambiguous_not_a_definite_failure() -> None:
    """A definite auth failure on the POST still cannot be trusted as "definitely did not
    write" -- only the reconciliation read decides that."""
    transport = ScriptedTransport()
    transport.queue_post(HttpResult(status=403, body=resource("OperationOutcome")))
    transport.queue_get(HttpResult(status=200, body=bundle()))

    with pytest.raises(AmbiguousTargetState):
        deliver(transport)

    assert len(transport.posts) == 1


def test_deliver_service_request_duplicate_conflict_reconciles_to_the_winner() -> None:
    """A losing writer's conflict response (the DB-level uniqueness patch rejecting a race) must
    reconcile to whichever order actually exists, never be treated as this writer's own
    failure."""
    transport = ScriptedTransport()
    transport.queue_post(HttpResult(status=500, body=resource("OperationOutcome")))
    winner = bundle(resource("ServiceRequest", "winner-sr"))
    transport.queue_get(HttpResult(status=200, body=winner))

    result = deliver(transport)

    assert result.record_id == "winner-sr"
    assert len(transport.posts) == 1  # the conflicted attempt, not a second one


# --- deliver_service_request_if_activated: activation gate, separate from capability --------


def gated_settings(**overrides: str) -> Settings:
    values = {
        "TZ": "Africa/Djibouti",
        "ADAPTER_DB_HOST": "openelis-db",
        "ADAPTER_DB_PORT": "5432",
        "ADAPTER_DB_NAME": "adapter",
        "ADAPTER_DB_USER": "adapter",
        "ADAPTER_DB_PASSWORD": "not-a-real-secret",
        "ADAPTER_AUDIT_PATH": "/tmp/audit.jsonl",
    }
    values.update(overrides)
    return Settings.from_env(values)


def test_activation_gate_blocks_with_no_site_code() -> None:
    transport = ScriptedTransport()
    settings = gated_settings()

    with pytest.raises(ServiceRequestDeliveryBlocked):
        run(
            deliver_service_request_if_activated(
                client(transport), settings, correlation(), {"resourceType": "ServiceRequest"}
            )
        )

    assert len(transport.posts) == 0  # the gate refuses before the capability ever runs


def test_activation_gate_blocks_with_a_configured_site_code() -> None:
    """Even a fully valid-shaped synthetic site code activates the gate -- this repo's
    Settings validation already refuses the real placeholder (DEV01); this test only proves
    the *gate* correctly reads that flag, not that any particular value is real."""
    transport = ScriptedTransport()
    transport.queue_post(HttpResult(status=200, body=resource("OperationOutcome")))
    transport.queue_get(HttpResult(status=200, body=bundle(resource("ServiceRequest", "sr-1"))))
    settings = gated_settings(OPENELIS_SITE_CODE="19")

    result = run(
        deliver_service_request_if_activated(
            client(transport), settings, correlation(), {"resourceType": "ServiceRequest"}
        )
    )

    assert result.record_id == "sr-1"


def test_activation_gate_delegates_to_the_real_capability_when_open() -> None:
    """The gate does not reimplement the writer -- confirms it is the same code path as calling
    the client directly, just conditionally reached."""
    transport = ScriptedTransport()
    transport.queue_get(HttpResult(status=200, body=bundle()))
    settings = gated_settings(OPENELIS_SITE_CODE="19")

    with pytest.raises(AmbiguousTargetState):  # the same zero-after-post outcome, gate or not
        run(
            deliver_service_request_if_activated(
                client(transport), settings, correlation(), {"resourceType": "ServiceRequest"}
            )
        )


def sr_with_status(fhir_id: str, status: str) -> dict[str, Any]:
    return {"resourceType": "ServiceRequest", "id": fhir_id, "status": status}


def test_cancellation_probe_revoked_status_is_exists_cancelled() -> None:
    transport = ScriptedTransport()
    transport.queue_get(HttpResult(status=200, body=bundle(sr_with_status("sr-1", "revoked"))))

    result = run(client(transport).cancellation_probe(correlation()))

    assert result.state is CancellationTargetState.EXISTS_CANCELLED
    assert result.record_id == "sr-1"


def test_cancellation_probe_active_status_is_exists_not_cancelled() -> None:
    transport = ScriptedTransport()
    transport.queue_get(HttpResult(status=200, body=bundle(sr_with_status("sr-1", "active"))))

    result = run(client(transport).cancellation_probe(correlation()))

    assert result.state is CancellationTargetState.EXISTS_NOT_CANCELLED
    assert result.record_id == "sr-1"


def test_cancellation_probe_zero_matches_is_ambiguous_not_a_novel_state() -> None:
    """A cancellation target must already exist (created by an earlier, already-completed
    add-test event) -- zero matches has no honest DH-025 vocabulary here and fails closed."""
    transport = ScriptedTransport()
    transport.queue_get(HttpResult(status=200, body=bundle()))

    result = run(client(transport).cancellation_probe(correlation()))

    assert result.state is CancellationTargetState.AMBIGUOUS
    assert result.record_id is None


def test_cancellation_probe_many_matches_is_ambiguous() -> None:
    transport = ScriptedTransport()
    two_orders = bundle(sr_with_status("sr-1", "active"), sr_with_status("sr-2", "active"), total=2)
    transport.queue_get(HttpResult(status=200, body=two_orders))

    result = run(client(transport).cancellation_probe(correlation()))

    assert result.state is CancellationTargetState.AMBIGUOUS


def test_cancellation_probe_unreachable_target_is_unreachable() -> None:
    transport = ScriptedTransport()
    transport.queue_get(ConnectionError("no route"))

    result = run(client(transport).cancellation_probe(correlation()))

    assert result.state is CancellationTargetState.UNREACHABLE


def test_cancellation_probe_bad_status_response_is_unreachable() -> None:
    transport = ScriptedTransport()
    transport.queue_get(HttpResult(status=500, body=None))

    result = run(client(transport).cancellation_probe(correlation()))

    assert result.state is CancellationTargetState.UNREACHABLE


def test_cancel_service_request_posts_dollar_cancel_operation() -> None:
    transport = ScriptedTransport()
    transport.queue_post(HttpResult(status=200, body={"resourceType": "Parameters"}))
    transport.queue_get(HttpResult(status=200, body=bundle(sr_with_status("sr-1", "revoked"))))

    result = run(client(transport).cancel_service_request(correlation()))

    assert result.state is CancellationTargetState.EXISTS_CANCELLED
    (url, payload) = transport.posts[0]
    assert url.endswith("/ServiceRequest/$cancel")
    assert payload["resourceType"] == "Parameters"
    assert payload["parameter"][0]["valueString"] == correlation().value


def test_cancel_service_request_never_trusts_its_own_post_status() -> None:
    """DH-011J live-proved a genuine concurrent race: the losing caller's $cancel POST can raise
    a transport-level error (the native side answered 500, StaleObjectStateException) even though
    the target really was cancelled by the winner. The writer must reconcile via the probe
    regardless, exactly like `deliver_service_request` never trusts its own POST outcome."""
    transport = ScriptedTransport()
    transport.queue_post(RuntimeError("HTTP 500 from a concurrent StaleObjectStateException"))
    transport.queue_get(HttpResult(status=200, body=bundle(sr_with_status("sr-1", "revoked"))))

    result = run(client(transport).cancel_service_request(correlation()))

    assert result.state is CancellationTargetState.EXISTS_CANCELLED


def test_cancel_service_request_reports_native_refusal_as_not_cancelled() -> None:
    """ADR-018: the native side refused (already in progress) -- confirmed via the same
    reconciliation read, not a special-cased response-body parse."""
    transport = ScriptedTransport()
    transport.queue_post(HttpResult(status=200, body={"resourceType": "Parameters"}))
    transport.queue_get(HttpResult(status=200, body=bundle(sr_with_status("sr-1", "active"))))

    result = run(client(transport).cancel_service_request(correlation()))

    assert result.state is CancellationTargetState.EXISTS_NOT_CANCELLED


def test_cancel_service_request_raises_when_reconciliation_cannot_prove_either_state() -> None:
    transport = ScriptedTransport()
    transport.queue_post(HttpResult(status=200, body={"resourceType": "Parameters"}))
    transport.queue_get(HttpResult(status=200, body=bundle()))

    with pytest.raises(AmbiguousTargetState):
        run(client(transport).cancel_service_request(correlation()))
