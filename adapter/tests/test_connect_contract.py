"""DH-CONNECT-CONTRACT-001 §67/§68 — the message contract, and the identities it scopes.

These tests are about what the contract *refuses*. A schema that accepts a naive timestamp, an
unknown field, an unscoped patient identifier or a business acceptance with no order attached has
not made anything safer than a dictionary would; the refusals are the whole product here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from daher_adapter.connect.contract import (
    CONTRACT_VERSION,
    BusinessAcceptance,
    Capability,
    Direction,
    Envelope,
    InMemoryReceiptStore,
    MessageType,
    OrderNotification,
    OrderSubmission,
    ReleasedResult,
    ResultObservation,
    ScopedExternalId,
    SourcePractitionerRef,
    TestMapping,
    contract_major,
    fingerprint,
    mapping_fingerprint,
    message_direction,
    optional_capabilities,
    required_capabilities,
)

ORG_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
ORG_B = uuid.UUID("22222222-2222-2222-2222-222222222222")
NOW = datetime(2026, 9, 22, 8, 0, tzinfo=UTC)


def _envelope(**overrides):
    payload = {
        "contract_version": CONTRACT_VERSION,
        "message_id": uuid.uuid4(),
        "connector_installation_id": uuid.uuid4(),
        "organization_id": ORG_A,
        "source_system": "openelis",
        "source_event_id": "evt-1",
        "source_occurred_at": NOW,
        "sent_at": NOW + timedelta(minutes=5),
        "lab_order_id": uuid.uuid4(),
        "payload": BusinessAcceptance(external_order_id="EXT-1"),
    }
    payload.update(overrides)
    return Envelope(**payload)


def _order_notification() -> OrderNotification:
    return OrderNotification(
        patient=ScopedExternalId(
            source_organization_id=ORG_B,
            source_system="emr",
            source_identity_system="urn:emr:mrn",
            source_identity_value="P-1",
        ),
        practitioner=SourcePractitionerRef(
            source_organization_id=ORG_B,
            source_system="emr",
            source_identity_system="urn:emr:staff",
            source_identity_value="D-1",
        ),
        catalog_codes=("GLU",),
    )


class TestContractVersion:
    def test_MC1_a_different_major_version_is_refused_at_the_boundary(self):
        """§57: no automatic carry-forward. A connector on contract 2 is not parsed hopefully."""
        with pytest.raises(ValidationError) as error:
            _envelope(contract_version="2.0")
        assert "requalification" in str(error.value)

    def test_MC2_an_unparseable_version_raises_rather_than_defaulting(self):
        for bad in ("", "one", "1", "x.y"):
            with pytest.raises(ValueError):
                contract_major(bad)


class TestEnvelopeValidation:
    def test_MC3_unknown_fields_are_refused_not_ignored(self):
        """``extra="forbid"`` because this envelope gates clinical routing.

        Silently dropping a field would let a partner believe they communicated something that
        arrived nowhere -- the worst outcome, because nobody finds out until a result is missing.
        """
        with pytest.raises(ValidationError):
            _envelope(priority="STAT")

    def test_MC4_naive_timestamps_are_refused(self):
        """An instant with no offset is not a fact about when; Djibouti is UTC+3."""
        with pytest.raises(ValidationError):
            _envelope(source_occurred_at=datetime(2026, 9, 22, 8, 0))
        with pytest.raises(ValidationError):
            _envelope(sent_at=datetime(2026, 9, 22, 8, 0))

    def test_MC5_source_event_time_stays_distinct_from_transmission_time(self):
        """§12/Q5: a connector draining a backlog after an outage.

        Everything it sends has a ``sent_at`` of today and a ``source_occurred_at`` of whenever the
        work really happened. One field could not carry both, and collapsing them would silently
        rewrite a week of clinical chronology as having happened at reconnection.
        """
        envelope = _envelope(source_occurred_at=NOW - timedelta(days=6), sent_at=NOW)
        assert envelope.source_occurred_at < envelope.sent_at
        assert (envelope.sent_at - envelope.source_occurred_at).days == 6

    def test_MC6_a_message_about_an_order_must_name_the_order(self):
        with pytest.raises(ValidationError) as error:
            _envelope(lab_order_id=None)
        assert "never inferred from an external identifier" in str(error.value)

    def test_MC7_an_order_submission_must_not_claim_an_order_already_exists(self):
        submission = OrderSubmission(
            patient=ScopedExternalId(
                source_organization_id=ORG_A,
                source_system="emr",
                source_identity_system="MRN",
                source_identity_value="123",
            ),
            practitioner=SourcePractitionerRef(
                source_organization_id=ORG_A,
                source_system="emr",
                source_identity_system="NPI",
                source_identity_value="P-9",
            ),
            external_order_id="EXT-1",
            source_test_codes=("GLU",),
        )
        with pytest.raises(ValidationError):
            _envelope(payload=submission)
        # The same submission with no order id is fine -- it is asking DHealth to create one.
        assert _envelope(payload=submission, lab_order_id=None) is not None


class TestExternalIdentityScoping:
    def test_ID1_the_same_local_id_at_two_facilities_is_two_people(self):
        """§14/§68/Q4. ``123`` is not an identifier; ``123 at this facility, from this system`` is.

        The Exchange learned this the expensive way -- ``PatientReference`` is unique on all four
        parts precisely because two facilities numbering their patients from 1 would otherwise be
        merged into one person. The contract carries the same four so the bridge can hand them to
        that repository unchanged rather than inventing a competing identity model.
        """
        at_a = ScopedExternalId(
            source_organization_id=ORG_A,
            source_system="emr",
            source_identity_system="MRN",
            source_identity_value="123",
        )
        at_b = at_a.model_copy(update={"source_organization_id": ORG_B})
        assert at_a.scope_key() != at_b.scope_key()

    def test_ID2_the_same_local_id_from_two_systems_is_two_people(self):
        from_emr = ScopedExternalId(
            source_organization_id=ORG_A,
            source_system="emr",
            source_identity_system="MRN",
            source_identity_value="123",
        )
        from_lis = from_emr.model_copy(update={"source_system": "lis"})
        assert from_emr.scope_key() != from_lis.scope_key()

    def test_ID3_there_is_no_demographic_field_to_match_on(self):
        """No name, no date of birth, no phone -- so no fuzzy match is even expressible.

        The strongest form of "no fuzzy patient matching" is a model that cannot represent the
        inputs a fuzzy match would need. Anyone wanting to add one would have to add the fields
        first, which is a visible change rather than a quiet one.
        """
        fields = set(ScopedExternalId.model_fields)
        assert fields == {
            "source_organization_id",
            "source_system",
            "source_identity_system",
            "source_identity_value",
        }
        for forbidden in ("display_name", "date_of_birth", "phone", "name", "dob"):
            assert forbidden not in fields


class TestFingerprints:
    def test_FP1_key_order_and_whitespace_do_not_change_the_fingerprint(self):
        assert fingerprint({"a": 1, "b": 2}) == fingerprint({"b": 2, "a": 1})

    def test_FP2_one_changed_value_changes_the_fingerprint(self):
        assert fingerprint({"endpoint": "a"}) != fingerprint({"endpoint": "b"})

    def test_FP3_equal_instants_in_different_offsets_fingerprint_equally(self):
        """Two clients writing the same moment with different offsets mean the same thing."""
        utc = datetime(2026, 9, 22, 8, 0, tzinfo=UTC)
        djibouti = datetime(2026, 9, 22, 11, 0, tzinfo=timezone(timedelta(hours=3)))
        assert fingerprint({"at": utc}) == fingerprint({"at": djibouti})

    def test_FP4_a_naive_datetime_cannot_be_fingerprinted(self):
        with pytest.raises(ValueError):
            fingerprint({"at": datetime(2026, 9, 22, 8, 0)})

    def test_FP5_mapping_fingerprint_is_order_independent_but_content_sensitive(self):
        """§56: reordering a mapping is not a change; retargeting one row is."""
        a = TestMapping(
            source_system="lis", source_code="GLU", catalog_entry_id=ORG_A, catalog_code="GLU"
        )
        b = TestMapping(
            source_system="lis", source_code="CBC", catalog_entry_id=ORG_B, catalog_code="CBC"
        )
        assert mapping_fingerprint([a, b]) == mapping_fingerprint([b, a])
        retargeted = b.model_copy(update={"catalog_entry_id": ORG_A})
        assert mapping_fingerprint([a, b]) != mapping_fingerprint([a, retargeted])
        assert mapping_fingerprint([a, b]) != mapping_fingerprint([a])

    def test_FP6_fingerprints_are_stable_across_processes(self):
        """Never Python's ``hash()``, which is per-process randomized for strings.

        A qualification pinned to a randomized hash would expire on restart for no reason, which
        would train everybody to ignore staleness -- the opposite of the intent.
        """
        assert fingerprint({"endpoint": "https://lis.invalid"}) == (
            fingerprint({"endpoint": "https://lis.invalid"})
        )
        assert len(fingerprint({"a": 1})) == 64  # SHA-256 hex


class TestCapabilityBundles:
    def test_CB1_the_lab_bundle_is_the_round_trip_and_nothing_more(self):
        """§10/§39. Required because their absence breaks the journey; optional because it
        does not."""
        required = required_capabilities("LAB")
        assert required == {
            Capability.ORDER_RECEIPT,
            Capability.BUSINESS_ACCEPTANCE,
            Capability.COLLECTION_EVENT,
            Capability.RELEASED_RESULT_PUBLICATION,
            Capability.AMENDMENT_PUBLICATION,
        }
        # Collection is required, and the reasoning is worth pinning rather than leaving in a
        # docstring: `LabWorkflowRepository` refuses ACCEPTED -> IN_PROGRESS with no recorded
        # collection milestone, and result entry refuses anything but IN_PROGRESS. A laboratory
        # connector that cannot report collection cannot enter a result at all.
        assert Capability.COLLECTION_EVENT not in optional_capabilities("LAB")
        # Processing start genuinely is optional -- nothing in the Exchange gates on it.
        assert Capability.PROCESSING_EVENT in optional_capabilities("LAB")

    def test_CB2_the_provider_bundle_requires_receiving_results_and_amendments(self):
        required = required_capabilities("PROVIDER")
        assert required == {
            Capability.ORDER_SUBMISSION,
            Capability.RELEASED_RESULT_RECEIPT,
            Capability.AMENDMENT_RECEIPT,
        }
        assert Capability.REVIEW_REPORTING in optional_capabilities("PROVIDER")

    def test_CB3_an_unknown_organization_type_raises_rather_than_qualifying_trivially(self):
        """An empty requirement would make an unknown type trivially qualified -- fail open."""
        with pytest.raises(ValueError):
            required_capabilities("PHARMACY")


class TestReplaySemantics:
    def test_RP1_an_identical_replay_is_recognised(self):
        store = InMemoryReceiptStore()
        envelope = _envelope()
        assert store.seen(envelope.replay_scope(), envelope.source_event_id) is None
        store.record(
            envelope.replay_scope(), envelope.source_event_id, envelope.content_fingerprint()
        )
        # A genuine retry: new message id, new transmission time, same event and same content.
        retry = envelope.model_copy(
            update={"message_id": uuid.uuid4(), "sent_at": envelope.sent_at + timedelta(hours=1)}
        )
        assert (
            store.seen(retry.replay_scope(), retry.source_event_id) == retry.content_fingerprint()
        )

    def test_RP2_different_content_under_the_same_event_identity_is_detectable(self):
        envelope = _envelope()
        conflicting = envelope.model_copy(
            update={"payload": BusinessAcceptance(external_order_id="DIFFERENT")}
        )
        assert envelope.content_fingerprint() != conflicting.content_fingerprint()

    def test_RP3_the_replay_scope_separates_facilities_and_source_systems(self):
        """Q4: the millionth connector numbering its events from 1 must not collide with the
        first."""
        base = _envelope()
        assert (
            base.replay_scope() != base.model_copy(update={"organization_id": ORG_B}).replay_scope()
        )
        assert (
            base.replay_scope()
            != base.model_copy(update={"source_system": "other-lis"}).replay_scope()
        )


class TestResultSemantics:
    def test_RS1_a_result_carries_the_sources_own_code_unit_and_range(self):
        """§18: source-flexible. DHealth records what it was told and invents nothing."""
        obs = ResultObservation(
            source_code="GLU",
            display_label="Glucose",
            result_type="NUMERIC",
            numeric_value=Decimal("5.4"),
            numeric_unit="mmol/L",
            reference_range_low=Decimal("3.9"),
            reference_range_high=Decimal("5.6"),
        )
        assert obs.numeric_unit == "mmol/L"
        # No field exists for an interpretation, and that is the point: DHealth records and routes,
        # it does not diagnose. Adding one would be a visible change to this model.
        for forbidden in ("abnormal_flag", "interpretation", "severity", "diagnosis"):
            assert forbidden not in ResultObservation.model_fields

    def test_RS2_a_released_result_must_state_that_the_source_released_it(self):
        """§19: ``source_release_confirmed`` is how a connector says it has a real signal.

        Required, not defaulted. A connector that simply forgot the field cannot accidentally
        publish a preliminary value as released.
        """
        assert ResultObservation is not None
        with pytest.raises(ValidationError):
            ReleasedResult(
                external_order_id="EXT-1",
                external_report_id="RPT-1",
                observations=(
                    ResultObservation(
                        source_code="GLU",
                        display_label="Glucose",
                        result_type="TEXT",
                        text_value="normal",
                    ),
                ),
            )


class TestDirectionAndOutboundNotification:
    """DH-CONNECT-AGENT-001 §5 — contract 1.1's one addition, and what makes it additive."""

    def test_DI1_every_message_type_declares_a_direction(self):
        """A type with no declared direction would pass both of the runtime's boundary checks.

        The runtime refuses a connector submitting an outbound type and refuses DHealth queueing an
        inbound one. Both checks read this table, so a type missing from it is a type nothing
        constrains -- fail-open, and invisible until somebody sends one.
        """
        for message_type in MessageType:
            assert message_direction(message_type) in set(Direction)

    def test_DI2_contract_1_1_is_a_minor_bump_so_nothing_requalifies(self):
        """§49: a contract *major* mismatch fails closed. 1.0 -> 1.1 must not be one.

        A connector built against 1.0 keeps working; it simply cannot receive orders, which was
        already true. Had this been 2.0, every existing installation would have gone
        ``CONTRACT_MAJOR_MISMATCH`` on the strength of an added field nobody had to implement.
        """
        assert CONTRACT_VERSION == "1.1"
        assert contract_major("1.0") == contract_major(CONTRACT_VERSION)
        _envelope(contract_version="1.0")

    def test_DI3_the_seven_1_0_message_types_still_travel_inbound(self):
        """The version bump added a direction; it did not re-point an existing one."""
        inbound = {
            MessageType.ORDER_SUBMISSION,
            MessageType.TECHNICAL_ACK,
            MessageType.BUSINESS_ACCEPTANCE,
            MessageType.BUSINESS_REJECTION,
            MessageType.COLLECTION_EVENT,
            MessageType.RELEASED_RESULT,
            MessageType.RELEASED_AMENDMENT,
        }
        for message_type in inbound:
            assert message_direction(message_type) is Direction.CONNECTOR_TO_DHEALTH
        assert message_direction(MessageType.ORDER_NOTIFICATION) is Direction.DHEALTH_TO_CONNECTOR

    def test_DI4_an_order_notification_must_name_the_dhealth_order(self):
        """It is about an order DHealth already created, so the correlation is not optional.

        The same rule the six other order-bound types obey. A notification without it would leave
        the connector's eventual acceptance with nothing to correlate back to but an external
        identifier DHealth never issued.
        """
        with pytest.raises(ValidationError):
            _envelope(payload=_order_notification(), lab_order_id=None)

        envelope = _envelope(payload=_order_notification(), lab_order_id=uuid.uuid4())
        assert envelope.payload.message_type is MessageType.ORDER_NOTIFICATION

    def test_DI5_an_order_notification_carries_no_demographics_or_clinical_content(self):
        """ADR-056 decision 5, restated as an assertion because 1.1 is where it was tempting.

        An outbound order is the first message DHealth composes rather than receives, which makes it
        the first place DHealth could volunteer patient data nobody asked for. The fields below are
        the ones a LIS integration would plausibly request; each must be raised by a named partner
        in DH-CONNECT-ADAPTER-001, not added here on spec.
        """
        for forbidden in (
            "date_of_birth",
            "birth_date",
            "sex",
            "gender",
            "address",
            "phone",
            "contact_value",
            "diagnosis",
            "clinical_notes",
            "notes",
            "indication",
        ):
            assert forbidden not in OrderNotification.model_fields
        assert set(OrderNotification.model_fields) == {
            "message_type",
            "patient",
            "practitioner",
            "catalog_codes",
        }

    def test_DI6_an_order_notification_needs_at_least_one_catalog_code(self):
        """An order for nothing is not an order, and a connector cannot ask what was meant."""
        with pytest.raises(ValidationError):
            OrderNotification(
                patient=ScopedExternalId(
                    source_system="emr",
                    source_identity_system="urn:emr:mrn",
                    source_identity_value="P-1",
                ),
                practitioner=SourcePractitionerRef(
                    source_system="emr",
                    source_identity_system="urn:emr:staff",
                    source_identity_value="D-1",
                ),
                catalog_codes=(),
            )
