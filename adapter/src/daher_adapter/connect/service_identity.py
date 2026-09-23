"""DH-CONNECT-AGENT-001 §7–§11 — the connector agent as its own principal class.

**A connector agent is not a user.** §7 lists what it must not be: CLINICIAN, LAB_TECH,
LAB_RESULT_RELEASER, ORG_ADMIN, platform_admin. That is not a permissions detail, it is the
difference between two kinds of actor. A clinician's authority comes from a person having been given
a role at a facility; an agent's comes from a machine holding a credential issued for exactly one
installation. Reusing the human principal would have meant every future authorization check had to
remember which kind of caller it was looking at, and one that forgot would let a machine act as a
professional -- or a professional act as a machine.

So ``ConnectorServicePrincipal`` is its own dataclass rather than ``AuthenticatedPrincipal`` with a
flag. A function that takes one cannot be handed the other; the type system does the remembering.

**§8 is the load-bearing rule: the installation comes from the credential, never from the request.**
An unauthenticated body naming an ``organization_id`` and a ``connector_installation_id`` is making
claims, not proving authority, and a connector that could change facility by editing a field would
be a cross-tenant clinical data path with no exploit required. The chain here runs one way only:

    bearer token -> validated signature, issuer, audience
                 -> azp (the OIDC client the token was issued to)
                 -> connector_installations.service_client_id  (unique, including retired rows)
                 -> installation -> organization

Nothing in a request body participates. The envelope's own ``organization_id`` is later *compared*
against what this chain produced and the message is refused on mismatch (§47) -- it is a consistency
check on the sender, never an input to the decision.

**§9: no credentials here.** DHealth stores ``service_client_id``, which is a public name -- the
same class of fact as a username -- and never the secret it authenticates. The secret is issued and
verified by the identity provider; nothing in this codebase can read it, log it, display it, or put
it in qualification evidence, because nothing in this codebase ever has it. §9's preference for "a
safe verifier or reference rather than recoverable plaintext" is satisfied by there being no
plaintext at all, and the Keycloak administration subsystem §9 warns against building does not
exist: provisioning the client is a deployment act, recorded here only by the identifier it
produced.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from daher_adapter.connect.models import ConnectorInstallation, ConnectorInstallationStatus
from daher_adapter.exchange.identity_auth import ValidatedToken


@dataclass(frozen=True, slots=True)
class ConnectorServicePrincipal:
    """An authenticated connector agent, bound to exactly one installation at one facility.

    Deliberately **not** a subclass of, or convertible to, ``AuthenticatedPrincipal``. There is no
    ``platform_admin`` field to be set, no membership, and no role: every authority this principal
    has is "act as this installation", and there is nothing else to express.

    ``identity_id`` is the ``ExchangeIdentity`` row the service account resolves to, present only so
    the existing clinical repositories -- which require an actor identity on every mutation -- can
    attribute what the agent does. It is an attribution handle, not a source of permission: the
    identity row grants nothing on its own, exactly as the comment on ``get_principal`` says of a
    human's.
    """

    installation_id: uuid.UUID
    organization_id: uuid.UUID
    service_client_id: str
    identity_id: uuid.UUID
    issuer: str
    subject: str


class ServiceIdentityError(RuntimeError):
    """Base class for a credential that will not resolve to a usable installation."""


class NotAServiceCredential(ServiceIdentityError):
    """The token is valid but is not a connector agent's.

    No ``azp``, an ``azp`` no installation claims, or a token carrying platform-admin authority.
    Mapped to ``401`` and to one indistinguishable message, following ``TokenValidationError``'s own
    discipline: a caller must not be able to probe which client ids are registered connectors by
    watching the error change.
    """


class InstallationNotUsable(ServiceIdentityError):
    """The credential resolves, and the installation may not carry traffic.

    Suspended or retired. Separated from ``NotAServiceCredential`` because this one *is* told to the
    caller in plain words: an agent whose installation was suspended should learn that and stop
    trying, rather than retry a 401 forever while an operator wonders why the queue is not draining.
    Nothing is leaked by saying so -- the caller already proved it holds that installation's
    credential.
    """

    def __init__(self, status: str) -> None:
        super().__init__(f"connector installation is {status}")
        self.status = status


def resolve_installation_by_client(
    session: Session, service_client_id: str
) -> ConnectorInstallation | None:
    """The installation this OIDC client authenticates for, whatever its status.

    Status is deliberately **not** filtered here. A retired installation's credential must produce a
    clear "retired" refusal rather than looking like an unknown client, because those are different
    problems: one is an agent nobody has switched off, the other is a credential nobody issued. The
    caller decides; this function only looks up.
    """
    return session.scalar(
        select(ConnectorInstallation).where(
            ConnectorInstallation.service_client_id == service_client_id
        )
    )


def resolve_service_principal(
    session: Session,
    token: ValidatedToken,
    *,
    identity_id: uuid.UUID,
    platform_admin_role: str = "dhealth-platform-admin",
) -> ConnectorServicePrincipal:
    """Turn a validated token into a connector principal, or refuse.

    ``identity_id`` is passed in rather than resolved here so that this function performs no writes:
    the caller creates or fetches the ``ExchangeIdentity`` (auditing a first sighting exactly as the
    human path does) and hands the id over. A resolver that quietly created rows would make an
    authentication *check* a mutation, and a failed check would leave evidence of a principal that
    was never admitted.

    The platform-admin refusal is §7 made mechanical. A client configured with the platform-admin
    realm role would otherwise get a service principal *and* be able to present the same token to
    the human endpoints as an administrator -- one credential, two authority classes, which is
    exactly the conflation this principal type exists to prevent. Refusing is right even though the
    misconfiguration is the deployment's fault: the safe direction is that such a token works
    nowhere rather than everywhere.
    """
    if token.has_role(platform_admin_role):
        raise NotAServiceCredential(
            "a connector agent credential may not carry platform-admin authority"
        )

    client_id = token.authorized_party
    if not client_id:
        raise NotAServiceCredential("token carries no authorized-party claim")

    installation = resolve_installation_by_client(session, client_id)
    if installation is None:
        raise NotAServiceCredential("no connector installation is bound to this client")

    if installation.status != ConnectorInstallationStatus.REGISTERED.value:
        raise InstallationNotUsable(installation.status)

    return ConnectorServicePrincipal(
        installation_id=installation.id,
        organization_id=installation.organization_id,
        service_client_id=client_id,
        identity_id=identity_id,
        issuer=token.issuer,
        subject=token.subject,
    )
