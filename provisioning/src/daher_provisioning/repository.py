"""Facility record storage.

In-memory for now, behind a narrow interface so a durable store slots in without the API
changing. It holds **provisioning records only** — identity, type, deploy target, state.
No clinical database credentials live here, which is the third and strongest layer of the
no-clinical-data guarantee (ADR-021): the service cannot read a patient record because it
has no way to reach one.
"""

from __future__ import annotations

from daher_provisioning.models import Facility, FacilityState


class FacilityExists(RuntimeError):
    """A facility with this id is already registered."""


class FacilityNotFound(RuntimeError):
    """No facility with this id."""


class FacilityRepository:
    def __init__(self) -> None:
        self._facilities: dict[str, Facility] = {}

    def add(self, facility: Facility) -> Facility:
        if facility.facility_id in self._facilities:
            # Reusing an id would point a "new" facility at an existing facility's volumes
            # (DH-026). Refuse rather than silently adopt someone else's database.
            raise FacilityExists(
                f"facility {facility.facility_id!r} already exists. Facility ids are "
                f"permanent and namespace the volumes; reusing one would attach this "
                f"facility to another's patient database."
            )
        self._facilities[facility.facility_id] = facility
        return facility

    def get(self, facility_id: str) -> Facility:
        try:
            return self._facilities[facility_id]
        except KeyError as exc:
            raise FacilityNotFound(f"no facility {facility_id!r}") from exc

    def list(self) -> list[Facility]:
        return sorted(self._facilities.values(), key=lambda f: f.facility_id)

    def set_state(self, facility_id: str, state: FacilityState) -> Facility:
        existing = self.get(facility_id)
        updated = existing.model_copy(update={"state": state})
        self._facilities[facility_id] = updated
        return updated
