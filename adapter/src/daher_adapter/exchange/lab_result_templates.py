"""DH-LAB-003/ADR-053: controlled, immutable, ``LabOffering``-scoped result-structure templates for
Native DHealth Lab result entry.

**Scope boundary the whole module enforces:** this is Native Lab *configuration*, never a global
Exchange observation requirement. ``LabObservation``/``record_observation``
(``exchange/results.py``) remain fully source-flexible -- a future DHealth Connect-reported
observation with no matching template here is not an error at that layer; templates are an
additive safety layer Native Lab's own human-entry UI (LAB-004) will require, not something the
low-level Exchange observation model is retroactively made to depend on.

**Template scope is ``LabOffering``, not ``TestCatalogEntry``.** `TestCatalogEntry` is the
provider-facing ordered *service* identity; `LabOffering` is one particular lab's own implementation
of that service. Two labs offering the identical catalog test may legitimately report a different
observation structure without that being a second ordered-service identity -- see
``LabResultTemplate``'s own docstring in ``models.py`` and this ticket's ADR for the full reasoning.

**Activation is optimistic-concurrency compare-and-swap**, mirroring
``CatalogRepository.set_price``'s own established discover-and-swap-in-one-statement discipline
(see that method's own docstring for why a separate prior ``SELECT`` is unsafe under PostgreSQL's
READ COMMITTED isolation): the owning ``LabOffering`` is locked ``FOR UPDATE`` first (this ticket's
own §27 instruction), then the current active row (if any) is discovered and deactivated in one
``UPDATE ... RETURNING`` before the target is activated. Already-active-target reactivation is
always a safe replay, regardless of what ``expected_current_template_id`` claimed -- identical to
``set_price``'s own "an already-correct value is always a safe no-op" exception.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.catalog import OfferingNotFound
from daher_adapter.exchange.models import (
    LabOffering,
    LabResultTemplate,
    LabResultTemplateObservation,
)


class TemplateNotFound(RuntimeError):
    """No ``LabResultTemplate`` matches the given id, or it exists but does not belong to the
    given offering -- both cases raised identically, mirroring ``OfferingNotFound``'s own
    convention for this repository's owning tenant boundary."""


class TemplateConflict(RuntimeError):
    """Same ``(lab_offering_id, version)`` already exists with a different observation-definition
    set -- never silently overwritten, never fuzzy-merged."""


class TemplateValidationError(ValueError):
    """The requested template/observation-definition set violates a structural rule -- caught
    before any row is inserted, so a rejected create is never partially committed."""


class TemplateActivationConflict(RuntimeError):
    """Optimistic-concurrency violation: ``expected_current_template_id`` does not match the
    actual currently-active template for this offering (or none exists when one was expected),
    and the actual active template is not already the requested target (which is instead a safe
    idempotent replay, never this conflict)."""


class ResultEntryPlanIntegrityError(RuntimeError):
    """DH-LAB-003 §38: raised by ``exchange/api.py``'s result-entry-plan orchestration when a
    *currently assigned* order's item references a catalog entry with no ``LabOffering`` at all at
    the selected lab -- structurally impossible given ``LabSelectionRepository.assign_by_provider``/
    ``select`` both require an ACTIVE offering for every requested item at assignment time, and
    offering rows are never deleted (only deactivated). Mirrors ``LabAssignmentIntegrityError``/
    ``PatientIdentityIntegrityError``'s own fail-closed discipline: never silently fabricate result
    structure to keep a screen working."""


@dataclass(frozen=True, slots=True)
class TemplateObservationDefinitionInput:
    source_code: str
    display_label: str
    result_type: str
    numeric_unit: str | None
    required: bool
    sort_order: int


@dataclass(frozen=True, slots=True)
class TemplateObservationDefinitionRecord:
    id: uuid.UUID
    source_code: str
    display_label: str
    result_type: str
    numeric_unit: str | None
    required: bool
    sort_order: int


@dataclass(frozen=True, slots=True)
class TemplateRecord:
    id: uuid.UUID
    lab_offering_id: uuid.UUID
    version: int
    active: bool
    created_at: datetime
    observations: tuple[TemplateObservationDefinitionRecord, ...]


def _validate_observations(observations: list[TemplateObservationDefinitionInput]) -> None:
    if not observations:
        raise TemplateValidationError("at least one observation definition is required")
    if not any(o.required for o in observations):
        raise TemplateValidationError("at least one observation definition must be required")
    seen_codes: set[str] = set()
    seen_sort_orders: set[int] = set()
    for o in observations:
        if not o.source_code.strip():
            raise TemplateValidationError("source_code is required")
        if not o.display_label.strip():
            raise TemplateValidationError("display_label is required")
        if o.result_type not in ("NUMERIC", "TEXT"):
            raise TemplateValidationError("result_type must be 'NUMERIC' or 'TEXT'")
        if o.result_type == "TEXT" and o.numeric_unit is not None:
            raise TemplateValidationError(
                "a TEXT observation definition must not carry numeric_unit"
            )
        if o.sort_order < 0:
            raise TemplateValidationError("sort_order must be >= 0")
        if o.source_code in seen_codes:
            raise TemplateValidationError(f"duplicate source_code {o.source_code!r} in template")
        seen_codes.add(o.source_code)
        if o.sort_order in seen_sort_orders:
            raise TemplateValidationError(f"duplicate sort_order {o.sort_order} in template")
        seen_sort_orders.add(o.sort_order)


def _normalize_inputs(
    observations: list[TemplateObservationDefinitionInput],
) -> tuple[tuple, ...]:
    return tuple(
        sorted(
            (
                o.sort_order,
                o.source_code,
                o.display_label,
                o.result_type,
                o.numeric_unit,
                o.required,
            )
            for o in observations
        )
    )


def _normalize_existing(observations: list[LabResultTemplateObservation]) -> tuple[tuple, ...]:
    return tuple(
        sorted(
            (
                o.sort_order,
                o.source_code,
                o.display_label,
                o.result_type,
                o.numeric_unit,
                o.required,
            )
            for o in observations
        )
    )


class LabResultTemplateRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    # -- create -------------------------------------------------------------------------------

    def create_template(
        self,
        *,
        lab_offering_id: uuid.UUID,
        version: int,
        observations: list[TemplateObservationDefinitionInput],
        session: Session | None = None,
    ) -> tuple[TemplateRecord, bool]:
        """Insert-first-wins on ``(lab_offering_id, version)``. Returns ``(record, created)``: an
        exact-content replay is a safe no-op; a same-version request with a different normalized
        observation-definition set raises ``TemplateConflict`` rather than silently overwriting
        version N. Atomic -- the template row and every observation-definition child commit or
        roll back together, in the same nested transaction."""
        if version < 1:
            raise TemplateValidationError("version must be >= 1")
        _validate_observations(observations)
        if session is not None:
            return self._create_template_in_session(
                session,
                lab_offering_id=lab_offering_id,
                version=version,
                observations=observations,
            )
        with self._sessions() as owned_session:
            record, created = self._create_template_in_session(
                owned_session,
                lab_offering_id=lab_offering_id,
                version=version,
                observations=observations,
            )
            owned_session.commit()
            return record, created

    def _create_template_in_session(
        self,
        session: Session,
        *,
        lab_offering_id: uuid.UUID,
        version: int,
        observations: list[TemplateObservationDefinitionInput],
    ) -> tuple[TemplateRecord, bool]:
        offering = session.get(LabOffering, lab_offering_id)
        if offering is None:
            raise OfferingNotFound(str(lab_offering_id))

        template = LabResultTemplate(lab_offering_id=lab_offering_id, version=version, active=False)
        try:
            with session.begin_nested():
                session.add(template)
                session.flush()  # (lab_offering_id, version) UniqueConstraint checked here first
                for definition in observations:
                    session.add(
                        LabResultTemplateObservation(
                            result_template_id=template.id,
                            source_code=definition.source_code,
                            display_label=definition.display_label,
                            result_type=definition.result_type,
                            numeric_unit=definition.numeric_unit,
                            required=definition.required,
                            sort_order=definition.sort_order,
                        )
                    )
                session.flush()
            session.refresh(template, attribute_names=["observations"])
            return _to_template_record(template), True
        except IntegrityError:
            existing = session.scalar(
                select(LabResultTemplate).where(
                    LabResultTemplate.lab_offering_id == lab_offering_id,
                    LabResultTemplate.version == version,
                )
            )
            if existing is None:
                raise
            if _normalize_existing(existing.observations) != _normalize_inputs(observations):
                raise TemplateConflict(
                    f"template for offering {lab_offering_id} v{version} already exists with "
                    f"different content"
                ) from None
            return _to_template_record(existing), False

    # -- read ---------------------------------------------------------------------------------

    def get_template(self, template_id: uuid.UUID) -> TemplateRecord:
        with self._sessions() as session:
            template = session.get(LabResultTemplate, template_id)
            if template is None:
                raise TemplateNotFound(str(template_id))
            return _to_template_record(template)

    def list_templates_for_offering(self, lab_offering_id: uuid.UUID) -> list[TemplateRecord]:
        with self._sessions() as session:
            templates = session.scalars(
                select(LabResultTemplate)
                .where(LabResultTemplate.lab_offering_id == lab_offering_id)
                .order_by(LabResultTemplate.version)
            ).all()
            return [_to_template_record(t) for t in templates]

    def get_active_templates_for_offerings(
        self, lab_offering_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, TemplateRecord]:
        """DH-LAB-003 §39: batched for the order-scoped result-entry-plan read -- exactly two
        queries regardless of how many offering ids are passed (one for active templates, one for
        their observation-definition children), never one pair per offering."""
        if not lab_offering_ids:
            return {}
        with self._sessions() as session:
            templates = session.scalars(
                select(LabResultTemplate).where(
                    LabResultTemplate.lab_offering_id.in_(lab_offering_ids),
                    LabResultTemplate.active.is_(True),
                )
            ).all()
            template_ids = [t.id for t in templates]
            observations_by_template: dict[uuid.UUID, list[LabResultTemplateObservation]] = {}
            if template_ids:
                rows = session.scalars(
                    select(LabResultTemplateObservation)
                    .where(LabResultTemplateObservation.result_template_id.in_(template_ids))
                    .order_by(LabResultTemplateObservation.sort_order)
                ).all()
                for row in rows:
                    observations_by_template.setdefault(row.result_template_id, []).append(row)
            return {
                t.lab_offering_id: _to_template_record(
                    t, observations=observations_by_template.get(t.id, [])
                )
                for t in templates
            }

    # -- activation -----------------------------------------------------------------------------

    def activate_template(
        self,
        *,
        lab_offering_id: uuid.UUID,
        template_id: uuid.UUID,
        expected_current_template_id: uuid.UUID | None,
        session: Session | None = None,
    ) -> tuple[TemplateRecord, str]:
        """Returns ``(record, outcome)`` where outcome is ``"created"`` (first active template
        for this offering), ``"updated"`` (switched from a different active template), or
        ``"unchanged"`` (the target was already active -- a safe replay, unconditionally)."""
        if session is not None:
            return self._activate_template_in_session(
                session,
                lab_offering_id=lab_offering_id,
                template_id=template_id,
                expected_current_template_id=expected_current_template_id,
            )
        with self._sessions.begin() as owned_session:
            return self._activate_template_in_session(
                owned_session,
                lab_offering_id=lab_offering_id,
                template_id=template_id,
                expected_current_template_id=expected_current_template_id,
            )

    def _activate_template_in_session(
        self,
        session: Session,
        *,
        lab_offering_id: uuid.UUID,
        template_id: uuid.UUID,
        expected_current_template_id: uuid.UUID | None,
    ) -> tuple[TemplateRecord, str]:
        offering = session.scalar(
            select(LabOffering).where(LabOffering.id == lab_offering_id).with_for_update()
        )
        if offering is None:
            raise OfferingNotFound(str(lab_offering_id))

        target = session.get(LabResultTemplate, template_id)
        if target is None or target.lab_offering_id != lab_offering_id:
            raise TemplateNotFound(str(template_id))

        # Discover-and-deactivate the current active row (if any) in one statement -- see this
        # module's own docstring for why a separate prior SELECT is unsafe under PostgreSQL's
        # READ COMMITTED isolation (identical reasoning to `CatalogRepository.set_price`).
        deactivated = session.execute(
            update(LabResultTemplate)
            .where(
                LabResultTemplate.lab_offering_id == lab_offering_id,
                LabResultTemplate.active.is_(True),
            )
            .values(active=False)
            .returning(LabResultTemplate.id)
        ).first()

        if deactivated is not None and deactivated.id == template_id:
            # The target was already the active template -- revert the deactivation within this
            # same transaction (invisible to everyone else) and report a clean replay, regardless
            # of what `expected_current_template_id` said (identical exception to `set_price`'s
            # own already-correct-value shortcut).
            session.execute(
                update(LabResultTemplate)
                .where(LabResultTemplate.id == template_id)
                .values(active=True)
            )
            session.flush()
            session.refresh(target, attribute_names=["active", "observations"])
            return _to_template_record(target), "unchanged"

        if expected_current_template_id is None:
            if deactivated is not None:
                raise TemplateActivationConflict(
                    f"a current active template already exists for offering {lab_offering_id}"
                )
        elif deactivated is None or deactivated.id != expected_current_template_id:
            raise TemplateActivationConflict(
                f"expected current template {expected_current_template_id} does not match the "
                f"actual current active template for offering {lab_offering_id}"
            )

        session.execute(
            update(LabResultTemplate).where(LabResultTemplate.id == template_id).values(active=True)
        )
        session.flush()
        session.refresh(target, attribute_names=["active", "observations"])
        outcome = "created" if deactivated is None else "updated"
        return _to_template_record(target), outcome


def _to_template_record(
    template: LabResultTemplate,
    *,
    observations: list[LabResultTemplateObservation] | None = None,
) -> TemplateRecord:
    source = observations if observations is not None else template.observations
    return TemplateRecord(
        id=template.id,
        lab_offering_id=template.lab_offering_id,
        version=template.version,
        active=template.active,
        created_at=template.created_at,
        observations=tuple(
            TemplateObservationDefinitionRecord(
                id=o.id,
                source_code=o.source_code,
                display_label=o.display_label,
                result_type=o.result_type,
                numeric_unit=o.numeric_unit,
                required=o.required,
                sort_order=o.sort_order,
            )
            for o in sorted(source, key=lambda o: o.sort_order)
        ),
    )
