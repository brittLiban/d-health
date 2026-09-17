"""DH-CLINIC-006/ADR-052: explicit provider-lab routing relationships -- the missing product
boundary behind ``LabSelectionRepository.assign_by_provider`` (DH-EX-010, ADR-043 launch option
(a)). Answers exactly one question: "may this provider route orders to this DHealth lab
organization?" Never "how does the order physically enter that lab's production system?" (a future
pilot-topology ticket's concern), and never "should this provider be able to browse this lab in the
public marketplace?" (``LabDirectoryProfile.listed`` stays completely independent -- see
``ProviderLabRoute``'s own docstring in ``models.py``).

``upsert`` is the same PUT-shaped, full-replace idempotent write already established by
``LabDirectoryProfileRepository.upsert`` -- a replay with an identical ``active`` value is a genuine
no-op (``changed=False``). Launch-1 route configuration is platform-admin-only; the authorization
gate lives in ``exchange/api.py``, not here -- this repository enforces only the structural
invariant (provider/lab organization-type correctness), matching the "repository is the enforcement
point" convention `LabOffering`/`LabDirectoryProfile` already established, so a caller that bypasses
the HTTP layer entirely still cannot create a type-inverted or malformed route.

``find_whole_order_eligible_labs`` mirrors ``exchange/catalog.py``'s marketplace method of the same
name -- same three-pass narrowing discipline -- but substitutes ``ProviderLabRoute.active`` for
``listed=True`` in the candidate join and never joins ``LabPrice`` anywhere. This read is advisory
UI data only (DH-CLINIC-006 §19): unlike the marketplace version it deliberately does **not** force
``REPEATABLE READ`` -- ``LabSelectionRepository.assign_by_provider`` is the sole authoritative
re-check, under its own row lock, and a lab that stops qualifying between this read and that
mutation is correctly refused there, not silently permitted here.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from daher_adapter.exchange.models import (
    LabDirectoryProfile,
    LabOffering,
    Organization,
    OrganizationStatus,
    OrganizationType,
    ProviderLabRoute,
    TestCatalogEntry,
)
from daher_adapter.exchange.organizations import OrganizationRepository
from daher_adapter.models import utc_now


class InvalidProviderLabRouteOrganizationType(ValueError):
    """``provider_organization_id`` does not reference a PROVIDER organization, or
    ``lab_organization_id`` does not reference a LAB organization (or either does not exist) --
    the repository-level check standing in for the cross-table CHECK constraint SQL cannot express
    portably, the same convention already used for ``LabOffering``/``LabDirectoryProfile``.
    Deliberately does not also require the referenced organization be ACTIVE: a route's *type*
    correctness is a permanent structural fact, but its organizations' operational status is
    re-checked fresh at eligibility/assignment time instead (DH-CLINIC-006 §19), so an org that
    later goes inactive does not permanently strand an existing route in a state that can no
    longer even be deactivated."""


@dataclass(frozen=True, slots=True)
class ProviderLabRouteRecord:
    id: uuid.UUID
    provider_organization_id: uuid.UUID
    lab_organization_id: uuid.UUID
    lab_organization_name: str
    active: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class EligibleProviderLab:
    """One row of "this provider is routed to this lab, and it can perform every requested test" --
    deliberately carries no price field of any kind (DH-CLINIC-006 §16/§32: this is operational
    routing, not a marketplace), and no ranking/ordering claim beyond a stable, deterministic sort
    by name."""

    lab_organization_id: uuid.UUID
    lab_organization_name: str
    public_address: str
    public_phone: str | None


class ProviderLabRouteRepository:
    def __init__(
        self, session_factory: sessionmaker[Session], organizations: OrganizationRepository
    ) -> None:
        self._sessions = session_factory
        self._organizations = organizations

    def upsert(
        self,
        *,
        provider_organization_id: uuid.UUID,
        lab_organization_id: uuid.UUID,
        active: bool,
        session: Session | None = None,
    ) -> tuple[ProviderLabRouteRecord, bool, bool]:
        """Returns ``(record, created, changed)``. Fails closed via
        `InvalidProviderLabRouteOrganizationType` if `provider_organization_id` is not a PROVIDER
        organization or `lab_organization_id` is not a LAB organization -- never silently accepts
        a swapped or nonexistent pair."""
        self._require_org_type(provider_organization_id, OrganizationType.PROVIDER)
        self._require_org_type(lab_organization_id, OrganizationType.LAB)
        if session is not None:
            return self._upsert_in_session(
                session,
                provider_organization_id=provider_organization_id,
                lab_organization_id=lab_organization_id,
                active=active,
            )
        with self._sessions() as owned_session:
            record, created, changed = self._upsert_in_session(
                owned_session,
                provider_organization_id=provider_organization_id,
                lab_organization_id=lab_organization_id,
                active=active,
            )
            owned_session.commit()
            return record, created, changed

    def _require_org_type(self, organization_id: uuid.UUID, expected: OrganizationType) -> None:
        record = self._organizations.get(organization_id)
        if record.org_type != expected.value:
            raise InvalidProviderLabRouteOrganizationType(
                f"organization {organization_id} is {record.org_type!r}, "
                f"expected {expected.value!r}"
            )

    def _upsert_in_session(
        self,
        session: Session,
        *,
        provider_organization_id: uuid.UUID,
        lab_organization_id: uuid.UUID,
        active: bool,
    ) -> tuple[ProviderLabRouteRecord, bool, bool]:
        existing = session.scalar(
            select(ProviderLabRoute)
            .where(
                ProviderLabRoute.provider_organization_id == provider_organization_id,
                ProviderLabRoute.lab_organization_id == lab_organization_id,
            )
            .with_for_update()
        )
        now = utc_now()
        if existing is None:
            route = ProviderLabRoute(
                provider_organization_id=provider_organization_id,
                lab_organization_id=lab_organization_id,
                active=active,
                created_at=now,
                updated_at=now,
            )
            try:
                with session.begin_nested():
                    session.add(route)
                    session.flush()
                return self._to_record(session, route), True, True
            except IntegrityError:
                # Lost a create race to a concurrent caller -- fall through and treat this as an
                # update against the row that now exists, never a second row.
                existing = session.scalar(
                    select(ProviderLabRoute)
                    .where(
                        ProviderLabRoute.provider_organization_id == provider_organization_id,
                        ProviderLabRoute.lab_organization_id == lab_organization_id,
                    )
                    .with_for_update()
                )
                if existing is None:
                    raise

        changed = existing.active != active
        if changed:
            existing.active = active
            existing.updated_at = now
            session.flush()
        return self._to_record(session, existing), False, changed

    def _to_record(self, session: Session, route: ProviderLabRoute) -> ProviderLabRouteRecord:
        lab_name = session.scalar(
            select(Organization.name).where(Organization.id == route.lab_organization_id)
        )
        return ProviderLabRouteRecord(
            id=route.id,
            provider_organization_id=route.provider_organization_id,
            lab_organization_id=route.lab_organization_id,
            lab_organization_name=lab_name or "",
            active=route.active,
            created_at=route.created_at,
            updated_at=route.updated_at,
        )

    def list_for_provider(
        self, provider_organization_id: uuid.UUID, *, active_only: bool = True
    ) -> list[ProviderLabRouteRecord]:
        """DH-CLINIC-006 §12: the provider's own routing directory -- deliberately never joins
        `LabPrice`, and `listed=False` labs are fully included (private/unlisted routing is a
        valid, intended state)."""
        with self._sessions() as session:
            query = (
                select(ProviderLabRoute, Organization.name)
                .join(Organization, Organization.id == ProviderLabRoute.lab_organization_id)
                .where(ProviderLabRoute.provider_organization_id == provider_organization_id)
            )
            if active_only:
                query = query.where(ProviderLabRoute.active.is_(True))
            rows = session.execute(query).all()
            return [
                ProviderLabRouteRecord(
                    id=route.id,
                    provider_organization_id=route.provider_organization_id,
                    lab_organization_id=route.lab_organization_id,
                    lab_organization_name=lab_name,
                    active=route.active,
                    created_at=route.created_at,
                    updated_at=route.updated_at,
                )
                for route, lab_name in rows
            ]

    def find_whole_order_eligible_labs(
        self, provider_organization_id: uuid.UUID, catalog_entry_ids: Sequence[uuid.UUID]
    ) -> list[EligibleProviderLab]:
        """DH-CLINIC-006 §13/§14: labs this provider is explicitly, currently routed to (an active
        `ProviderLabRoute`) that can also fulfil **every** requested catalog entry via an active
        `LabOffering`, never a partial match -- a lab covering only some ordered tests is excluded
        entirely, matching `assign_by_provider`'s own whole-order-only contract exactly (this
        method's final pass reuses the identical offering/catalog-entry conditions that method
        checks, so a lab this returns as eligible and a lab `assign_by_provider` will actually
        accept can never silently disagree about *why*, even though the assignment mutation still
        always re-verifies fresh rather than trusting this read -- see this module's own
        docstring)."""
        required_count = len(catalog_entry_ids)
        if required_count == 0:
            return []
        with self._sessions() as session:
            candidate_rows = session.execute(
                select(
                    LabOffering.lab_organization_id,
                    func.count(func.distinct(LabOffering.catalog_entry_id)).label("matched"),
                )
                .join(TestCatalogEntry, TestCatalogEntry.id == LabOffering.catalog_entry_id)
                .join(
                    ProviderLabRoute,
                    (ProviderLabRoute.lab_organization_id == LabOffering.lab_organization_id)
                    & (ProviderLabRoute.provider_organization_id == provider_organization_id)
                    & (ProviderLabRoute.active.is_(True)),
                )
                .where(
                    LabOffering.catalog_entry_id.in_(catalog_entry_ids),
                    LabOffering.active.is_(True),
                    TestCatalogEntry.active.is_(True),
                    TestCatalogEntry.is_synthetic.is_(True),
                )
                .group_by(LabOffering.lab_organization_id)
                .having(func.count(func.distinct(LabOffering.catalog_entry_id)) == required_count)
            ).all()
            candidate_lab_ids = [row.lab_organization_id for row in candidate_rows]
            if not candidate_lab_ids:
                return []

            directory_rows = session.execute(
                select(Organization, LabDirectoryProfile)
                .join(
                    LabDirectoryProfile, LabDirectoryProfile.lab_organization_id == Organization.id
                )
                .where(
                    Organization.id.in_(candidate_lab_ids),
                    Organization.status == OrganizationStatus.ACTIVE.value,
                    # `listed` is deliberately NOT checked here -- an unlisted-but-routed lab
                    # remains a valid, private provider-referral destination (DH-CLINIC-006 §17).
                    LabDirectoryProfile.accepting_orders.is_(True),
                )
            ).all()
            if not directory_rows:
                return []
            directory_by_lab = {
                organization.id: (organization, profile) for organization, profile in directory_rows
            }
            final_lab_ids = list(directory_by_lab.keys())

            # Re-verify every condition pass (1) already checked, not just re-trust it -- the same
            # defensive re-check discipline `catalog.find_whole_order_eligible_labs` and
            # `assign_by_provider` both apply. Deliberately no `LabPrice` join anywhere in this
            # method, unlike the marketplace version -- ADR-043 Law 1: price must never be a
            # prerequisite for provider-assigned routing (DH-CLINIC-006 §16).
            offering_rows = session.execute(
                select(LabOffering.lab_organization_id, LabOffering.catalog_entry_id)
                .join(TestCatalogEntry, TestCatalogEntry.id == LabOffering.catalog_entry_id)
                .join(
                    ProviderLabRoute,
                    (ProviderLabRoute.lab_organization_id == LabOffering.lab_organization_id)
                    & (ProviderLabRoute.provider_organization_id == provider_organization_id)
                    & (ProviderLabRoute.active.is_(True)),
                )
                .where(
                    LabOffering.lab_organization_id.in_(final_lab_ids),
                    LabOffering.catalog_entry_id.in_(catalog_entry_ids),
                    LabOffering.active.is_(True),
                    TestCatalogEntry.active.is_(True),
                    TestCatalogEntry.is_synthetic.is_(True),
                )
            ).all()
            entries_by_lab: dict[uuid.UUID, set[uuid.UUID]] = {}
            for lab_id, entry_id in offering_rows:
                entries_by_lab.setdefault(lab_id, set()).add(entry_id)

            requested_entry_ids = set(catalog_entry_ids)
            results: list[EligibleProviderLab] = []
            for lab_id in final_lab_ids:
                # Defensive: never emit a partial-lab result -- compare the exact requested *set*,
                # not just its size, mirroring `catalog.find_whole_order_eligible_labs`'s own
                # explicit rationale for the identical check.
                if entries_by_lab.get(lab_id, set()) != requested_entry_ids:
                    continue
                organization, profile = directory_by_lab[lab_id]
                results.append(
                    EligibleProviderLab(
                        lab_organization_id=organization.id,
                        lab_organization_name=organization.name,
                        public_address=profile.public_address,
                        public_phone=profile.public_phone,
                    )
                )
            results.sort(key=lambda lab: (lab.lab_organization_name, str(lab.lab_organization_id)))
            return results
