"""Registered deploy hosts (ADR-022).

``deploy_target`` is a field on the facility record (ADR-020) so the topology stays
flexible. But its **value must never come from an untrusted client**: a breached portal
that can name any host could point provisioning at an address it wants probed, or at a
host already running someone else's facility.

So the request names a host, and the API resolves that name against hosts an operator
registered here. A host this service does not know about cannot be deployed to, whatever
the caller asks for.

Registration itself is deliberately out of band — see ADR-022's bootstrap flow. A host
appears here because an operator enrolled it, not because a request mentioned it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from daher_provisioning.models import DeployTarget

HOST_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$")


class HostError(RuntimeError):
    """The requested deploy host is unknown, unusable, or already in use."""


@dataclass(frozen=True)
class RegisteredHost:
    host_id: str
    target: DeployTarget
    enrolled: bool = False
    # Set once a facility is deployed here. One facility per host is the default topology
    # (ADR-020, one VM per facility); a shared host would relax this deliberately.
    facility_id: str | None = None


@dataclass
class HostRegistry:
    _hosts: dict[str, RegisteredHost] = field(default_factory=dict)

    def register(self, host_id: str, target: DeployTarget) -> RegisteredHost:
        if not HOST_ID_PATTERN.match(host_id):
            raise HostError(
                f"host id {host_id!r} must be 3-64 characters of lowercase letters, digits "
                f"and hyphens"
            )
        if host_id in self._hosts:
            raise HostError(f"host {host_id!r} is already registered")
        host = RegisteredHost(host_id=host_id, target=target)
        self._hosts[host_id] = host
        return host

    def mark_enrolled(self, host_id: str) -> RegisteredHost:
        """Called once the agent on that host has bootstrapped (ADR-022)."""
        host = self.get(host_id)
        updated = RegisteredHost(
            host_id=host.host_id, target=host.target, enrolled=True, facility_id=host.facility_id
        )
        self._hosts[host_id] = updated
        return updated

    def get(self, host_id: str) -> RegisteredHost:
        try:
            return self._hosts[host_id]
        except KeyError as exc:
            raise HostError(
                f"host {host_id!r} is not registered. Deploy targets are resolved against "
                f"hosts an operator enrolled, never taken from the request — otherwise a "
                f"caller could name any address it liked (ADR-022)."
            ) from exc

    def claim(self, host_id: str, facility_id: str) -> RegisteredHost:
        """Bind a facility to a host, refusing if the host is already occupied.

        The refusal matters more than the binding. A create request that quietly reused an
        occupied host would overwrite a live facility's env file and point a "new" facility
        at an existing patient database — the same shape as the FACILITY_ID default trap,
        arriving through a request that looks entirely routine.
        """
        host = self.get(host_id)
        if not host.enrolled:
            raise HostError(
                f"host {host_id!r} is registered but its agent has not enrolled yet. "
                f"Run the bootstrap step on that host first (ADR-022)."
            )
        if host.facility_id is not None:
            raise HostError(
                f"host {host_id!r} already runs facility {host.facility_id!r}. Refusing to "
                f"deploy a second facility onto it: that would overwrite a live env file "
                f"and attach this facility to an existing patient database."
            )
        claimed = RegisteredHost(
            host_id=host.host_id, target=host.target, enrolled=True, facility_id=facility_id
        )
        self._hosts[host_id] = claimed
        return claimed

    def list(self) -> list[RegisteredHost]:
        return sorted(self._hosts.values(), key=lambda h: h.host_id)
