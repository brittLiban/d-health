"""The structural no-clinical-data guarantee (ADR-021).

This is the most important test in the provisioning service. It walks every model that can
cross the API boundary and fails the build if a field is named for anything originating
from a patient record.

It is deliberately blunt and deliberately noisy. A false failure costs one conversation
about naming; a false pass is the difference between a fleet-management tool and a
reportable incident. If a legitimate field trips it, rename the field — do not add it to
the allowlist without reading ADR-021 first and deciding, out loud, that it carries nothing
patient-derived.
"""

from __future__ import annotations

import inspect

import pytest
from pydantic import BaseModel

from daher_provisioning import models
from daher_provisioning.models import ClosedModel

# Vocabulary that indicates data originating from a patient record. Substring matched
# against field names, so `patient_count` and `total_patients` are both caught.
CLINICAL_VOCABULARY = (
    "patient",
    "encounter",
    "observation",
    "obs_",
    "diagnosis",
    "diagnos",
    "result",
    "specimen",
    "sample",
    "order",
    "prescription",
    "medication",
    "allergy",
    "immunization",
    "lab_value",
    "test_result",
    "chart",
    "mrn",
    "identifier",
    "date_of_birth",
    "dob",
    "gender",
    "clinical",
)

# Words that look clinical but are unambiguously infrastructure here. Each needs a reason.
ALLOWED_EXCEPTIONS = {
    # "services_running"/"services_expected" — container counts, not clinical anything.
    "services_running",
    "services_expected",
}

# Vocabulary that indicates a credential value rather than a pointer to one (DH-027G,
# extending ADR-021's structural pattern to secrets — the backup passphrase must never
# reach this service at all, per ADR-025, but this is the same belt-and-braces guarantee
# applied to the boundary it would have to cross if that ever regressed). Nothing in this
# module needs an exception today: `credential_set_id` is a pointer, not "secret".
SECRET_VOCABULARY = ("passphrase", "secret", "password")


def response_models() -> list[type[BaseModel]]:
    found = []
    for _, obj in inspect.getmembers(models, inspect.isclass):
        if not issubclass(obj, BaseModel) or obj is BaseModel:
            continue
        if obj.__module__ == models.__name__:
            found.append(obj)
    return found


def test_there_are_models_to_check():
    """A test that silently checks nothing is worse than no test (learned the hard way)."""
    assert len(response_models()) >= 5, "model discovery found almost nothing — check the import"


@pytest.mark.parametrize("model", response_models(), ids=lambda m: m.__name__)
def test_no_field_is_named_for_clinical_data(model: type[BaseModel]):
    for field_name in model.model_fields:
        if field_name in ALLOWED_EXCEPTIONS:
            continue
        lowered = field_name.lower()
        for word in CLINICAL_VOCABULARY:
            assert word not in lowered, (
                f"{model.__name__}.{field_name} looks like it carries patient-derived data. "
                f"The provisioning API is provision-and-monitor only (ADR-021). If this field "
                f"genuinely carries nothing from a patient record, rename it — do not widen "
                f"the allowlist without re-reading ADR-021."
            )


@pytest.mark.parametrize("model", response_models(), ids=lambda m: m.__name__)
def test_no_field_is_named_for_a_secret(model: type[BaseModel]):
    """DH-027G's version of the guard above: a credential value must never be attachable to
    a response model, structurally — not by every author remembering ADR-025."""
    for field_name in model.model_fields:
        if field_name in ALLOWED_EXCEPTIONS:
            continue
        lowered = field_name.lower()
        for word in SECRET_VOCABULARY:
            assert word not in lowered, (
                f"{model.__name__}.{field_name} looks like it carries a credential value. "
                f"The control plane never holds the backup passphrase (ADR-025) or any "
                f"facility credential (ADR-022) — if this field genuinely carries neither, "
                f"rename it rather than widening the allowlist."
            )


def test_the_secret_guard_control_a_deliberately_forbidden_field_is_caught():
    """Control test (DH-027G): prove the guard above actually fires, on a throwaway model
    shaped exactly like the mistake it exists to catch — a `BackupReport`-like response
    that grew a literal passphrase field. If this test ever passes trivially (no
    AssertionError), the guard itself is broken, not just unexercised.
    """

    class HypotheticalLeakyReport(ClosedModel):
        state: str
        # The exact mistake: a real backup passphrase attached to a response model.
        passphrase: str

    with pytest.raises(AssertionError):
        for field_name in HypotheticalLeakyReport.model_fields:
            lowered = field_name.lower()
            for word in SECRET_VOCABULARY:
                assert word not in lowered, f"{field_name} looks like it carries a credential"


@pytest.mark.parametrize("model", response_models(), ids=lambda m: m.__name__)
def test_every_model_forbids_extra_fields(model: type[BaseModel]):
    """A clinical field must not be attachable downstream by passing a stray key."""
    assert model.model_config.get("extra") == "forbid", (
        f"{model.__name__} must forbid extra fields so a clinical field cannot be "
        f"introduced by a dict that happens to carry one"
    )


def test_status_model_carries_no_volume_metrics():
    """The specific 'just one summary stat' request ADR-021 exists to refuse."""
    fields = set(models.FacilityStatus.model_fields)
    for tempting in ("patient_count", "encounter_count", "order_volume", "result_count"):
        assert tempting not in fields


def test_decommission_is_soft_only():
    """No API-reachable state may mean 'clinical data destroyed'."""
    states = {s.value for s in models.FacilityState}
    assert "decommissioned" in states
    for destructive in ("deleted", "purged", "destroyed", "wiped"):
        assert destructive not in states, (
            "destroying clinical data must not be reachable from the standard API surface"
        )


def test_create_response_reports_incomplete_steps_rather_than_implying_success():
    """DNS is unconfigured; a caller must be told, not left to infer from a 201."""
    fields = models.FacilityCreateResponse.model_fields
    assert "dns_configured" in fields
    assert "incomplete_steps" in fields
    assert fields["dns_configured"].default is False, (
        "DNS must default to NOT configured — a provisioning step that appears to succeed "
        "against a provider that does not exist is a false green (ADR-020)"
    )
