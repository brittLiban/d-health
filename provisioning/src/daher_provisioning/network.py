"""Application-level source allowlist (ADR-023).

The firewall is the primary control. This is the second one, because firewalls get edited —
usually by someone opening a port to debug at 2am and not closing it. If that happens, the
API still refuses and records the attempt rather than quietly becoming internet-facing.

⚠️ **The peer address only. Never `X-Forwarded-For`.** Any client can set that header, so
trusting it here would hand an attacker the allowlist bypass for free. If a reverse proxy is
ever placed in front of this service, the allowlist must be updated to the proxy's address
deliberately — not worked around by believing a header.
"""

from __future__ import annotations

import ipaddress
import logging

logger = logging.getLogger("daher_provisioning.network")


class SourceNotAllowed(RuntimeError):
    """The request came from an address outside the configured allowlist."""


class SourceAllowlist:
    """Allow a fixed set of CIDRs. An empty allowlist means 'no application-level check'."""

    def __init__(self, cidrs: list[str] | None) -> None:
        self._networks = []
        for entry in cidrs or []:
            # Accepts both a bare address and a CIDR; `strict=False` so 10.0.0.5/24 is not
            # an error, because typing a host address with a prefix is an easy slip and
            # failing on it would push someone toward disabling the check entirely.
            self._networks.append(ipaddress.ip_network(entry, strict=False))

        if not self._networks:
            logger.warning(
                "no application source allowlist configured — this service is relying "
                "ENTIRELY on network controls (ADR-023). If the firewall is ever opened, "
                "nothing here will refuse the traffic."
            )

    @property
    def enforced(self) -> bool:
        return bool(self._networks)

    def check(self, peer_address: str | None) -> None:
        """Raise unless the peer is allowed. An unknown peer is refused when enforcing.

        A missing address while enforcing is treated as a refusal rather than an allow: the
        alternative is that anything which obscures the peer becomes a bypass.
        """
        if not self._networks:
            return

        if not peer_address:
            raise SourceNotAllowed(
                "request has no determinable source address; refusing while an allowlist "
                "is enforced (ADR-023)"
            )

        try:
            address = ipaddress.ip_address(peer_address)
        except ValueError as exc:
            raise SourceNotAllowed(f"unparseable source address {peer_address!r}") from exc

        if not any(address in network for network in self._networks):
            raise SourceNotAllowed(
                f"source {peer_address} is not in the provisioning allowlist. The API is "
                f"not meant to be publicly reachable (ADR-023); if this is a legitimate "
                f"new caller, add it deliberately rather than disabling the check."
            )
