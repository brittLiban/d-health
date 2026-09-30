"""DHEALTH-ACCESS-CAPABILITIES-001/ADR-067: what a person may **do**, as distinct from what they
**are**.

── The one sentence ──

**Roles are templates. Capabilities are authority.**

A role is a job title — a useful human word, a sensible onboarding shortcut, and a terrible
security primitive. A capability is a permission. DHealth used to conflate them:
`require_clinician` asked *"are you a CLINICIAN"*, so every clinical call site hard-coded a job
title, and the only way to give one person one extra authority was to give them somebody else's
entire job.

That produced the failure this ticket exists to end. Two receptionists doing the same work, one of
whom also takes payment, could not be expressed at all without inventing `FRONT_DESK_WITH_BILLING`
and `FRONT_DESK_NO_BILLING` — and then `ADMIN_WITH_BILLING`, and then the combinatorial explosion
§62 forbids by name.

So every gate now asks a capability question, and this module is the single place that answers it.

── The three layers ──

1. **Capability** (`Capability` below) — a stable, DHealth-defined atomic authority. The vocabulary
   is *ours*, not the facility's: backend code must know exactly what each one means, so a facility
   cannot invent `clinic.custom_permission_239` and expect anything to honour it (§2). What a
   facility composes is **who receives which existing capabilities**.

2. **Role template** — a named bundle of capabilities. `SYSTEM_ROLE_TEMPLATES` below is the seed;
   the assignments live in `exchange_organization_role_assignments`, the table that already existed,
   because a role *is* a system template and re-pointing it is a rename rather than a migration.

3. **Member override** — one capability granted to, or denied from, one membership, on top of
   whatever its templates give. This is what lets two people with the same job have different access
   without a third job title.

── The effective-capability equation ──

    effective = (union of assigned templates' capabilities  ∪  direct GRANTs)  −  direct DENYs

**A DENY always wins.** It beats a template grant and it beats a direct grant — there is exactly one
precedence rule and no ordering subtlety, because an access model whose answer depends on the order
rows were written is one nobody can reason about at three in the morning. A membership that is not
ACTIVE, or whose organization is not ACTIVE, has **no** ordinary facility capabilities at all,
regardless of what any template or override says.

── What this module deliberately is not ──

Not a policy engine (§61). There is no expression language, no JSON policy, no CEL, no Rego, no
predicate a facility can author. There is a set of capabilities, a set of templates, a set of
overrides, and one function that intersects them. That is the whole model, and its being small is
the feature: authorization nobody can read is authorization nobody can audit.

Not a screen-permission system (§4). There is no `SCREEN_BILLING`, no `SHOW_RESULTS_TAB`. Navigation
derives from the same effective capabilities the backend enforces, so the two cannot drift — which
they would, immediately and silently, if screens were granted separately.

── Platform authority is not in here, on purpose ──

`PLATFORM_ADMIN` is a Keycloak realm role carried by the token, not an organization capability, and
§7 requires it stay that way. It is therefore *structurally* impossible for a facility administrator
to grant it through this module: there is no `Capability` member for it, so there is nothing to ask
for. That is stronger than a deny-list, and it is why NC-CAP-5 has to invent an enum member before
it can even attempt the escalation.

Machine identity (Connect), Meta HMAC and patient order-scoped grants are likewise outside this
model (§8). This is about human staff inside an organization.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from daher_adapter.exchange.models import OrganizationRole, OrganizationType


class Capability(StrEnum):
    """The authoritative capability catalog. One vocabulary, not one enum per feature (§9).

    **Derived from authority boundaries that already exist**, not from a wishlist. Every member
    below corresponds to a gate `api.py` was already enforcing on `790c736`; the catalog was built
    by walking that file's decorators and grouping its `_require_*` calls, so the migration is a
    renaming of existing authority rather than a redesign of it (§18).

    Where an existing gate guarded several distinct operations, it is split — but only along lines
    the product already draws, and only where a future composition plainly needs the seam. The
    `CLINICIAN` template receives every piece of the old clinician bundle, so the split changes
    nobody's authority today while making §64's point achievable: Front Desk can later be given
    `PATIENT_REGISTER` without also receiving `ORDER_MANAGE`, and that decision will not touch
    Billing, Visits or patient-directory code.

    Deliberately **not** split further. `LAB_QUEUE_WORK` covers accept, reject, collection,
    processing and result entry as one authority because the product has never separated them and
    inventing the seam now would be speculative (§3A: *"Do not create dozens of speculative future
    capabilities"*).
    """

    # -- clinical: the provider side ---------------------------------------------------------
    #: Search and read the facility's own patient directory and its patient references.
    PATIENT_DIRECTORY_READ = "patient.directory.read"
    #: Register a new patient, or bind a new patient reference.
    PATIENT_REGISTER = "patient.register"
    #: Read visits and their history, including the clinician's note. **Clinical.**
    VISIT_READ = "visit.read"
    #: Register a patient's arrival, see who is waiting, and cancel an arrival that ends before
    #: clinical contact. DHEALTH-FRONT-DESK-001/ADR-069 split this out of `VISIT_DOCUMENT`: an
    #: ordinary clinic day begins with somebody at the desk putting a patient into the waiting
    #: list, and that person needs no authority over the consultation that follows. It carries
    #: **no** clinical read — a holder cannot open a note, cannot see the stated reason, and
    #: cannot start a consultation.
    VISIT_CHECK_IN = "visit.check_in"
    #: Run a consultation: take a waiting patient, write notes, amend the stated reason, finish.
    #: Registering the arrival is `VISIT_CHECK_IN`; the `CLINICIAN` template carries both, so the
    #: authority a clinician held before ADR-069 is unchanged.
    VISIT_DOCUMENT = "visit.document"
    #: Create diagnostic orders and route them, and read the order surface that goes with doing so —
    #: intents, eligible labs, release versions, patient-access grants, notification retries.
    ORDER_MANAGE = "order.manage"
    #: Record a clinician's review disposition against a released result version.
    RESULT_REVIEW = "result.review"

    # -- clinical: the laboratory side -------------------------------------------------------
    #: The laboratory's own work on assigned orders: accept, reject, record collection, start
    #: processing, enter and correct result values.
    LAB_QUEUE_WORK = "lab.queue.work"
    #: **Protected.** Release results explicitly. ADR-044's separation of entry from release is
    #: safety-critical: a technician may record values for ever and never publish one.
    LAB_RESULT_RELEASE = "lab.result.release"

    # -- facility administration -------------------------------------------------------------
    #: Administer this organization's staff: memberships, role templates, capability overrides.
    #: The capability the last-administrator invariant is computed over (§22).
    STAFF_ACCESS_MANAGE = "staff.access.manage"
    #: Configure what the facility offers: catalogue offerings, result templates, directory profile.
    FACILITY_CONFIGURE = "facility.configure"

    # -- billing -----------------------------------------------------------------------------
    #: Run the till: invoices, payments, balances, receipts, and the narrow billing patient lookup.
    BILLING_OPERATE = "billing.operate"
    #: Decide what the facility charges: create services, set and change prices. Separate from
    #: `BILLING_OPERATE` as a financial control, not for tidiness — somebody who can quietly change
    #: a price *and* take the payment is the oldest problem in facility billing (ADR-066 §2).
    BILLING_CONFIGURE_PRICES = "billing.configure_prices"


class Grantability(StrEnum):
    """How a capability may be assigned. §10: *express existing restrictions rather than erase
    them.*"""

    #: An organization administrator may grant or deny it freely within their own organization.
    FACILITY = "FACILITY"
    #: Clinical authority with its own safety history. Assignable by a facility administrator under
    #: exactly the policy that already applied to the role it came from — no broader, no narrower.
    PROTECTED_CLINICAL = "PROTECTED_CLINICAL"


@dataclass(frozen=True, slots=True)
class CapabilityDefinition:
    """What a capability *is*, in the words a facility administrator reads.

    The French label and description live here rather than in a frontend file (§9) for two reasons:
    the backend serves them to the Team & Access screen so there is one source of truth, and a
    capability whose meaning is only written in a React component is a capability nobody can audit
    from the server.
    """

    key: Capability
    #: The short French label shown in `Équipe et accès`. Never the enum value: §9 forbids exposing
    #: internal vocabulary to facility staff.
    label: str
    #: One sentence, in French, saying what the holder may actually do.
    description: str
    #: The heading this capability is grouped under on screen.
    group: str
    #: Which organization types may hold it at all. A laboratory has no patient directory of its
    #: own (D-6 is open), so `PATIENT_DIRECTORY_READ` is provider-only and the server refuses to
    #: grant it at a laboratory rather than granting something inert.
    organization_types: frozenset[OrganizationType]
    grantability: Grantability = Grantability.FACILITY


_PROVIDER = frozenset({OrganizationType.PROVIDER})
_LAB = frozenset({OrganizationType.LAB})
_BOTH = frozenset({OrganizationType.PROVIDER, OrganizationType.LAB})


#: The catalog, in the order a facility administrator reads it on screen.
CAPABILITY_CATALOG: tuple[CapabilityDefinition, ...] = (
    CapabilityDefinition(
        key=Capability.PATIENT_DIRECTORY_READ,
        label="Consulter le répertoire des patients",
        description="Rechercher et ouvrir les dossiers patients de cet établissement.",
        group="Patients",
        organization_types=_PROVIDER,
    ),
    CapabilityDefinition(
        key=Capability.PATIENT_REGISTER,
        label="Enregistrer un patient",
        description="Créer un nouveau dossier patient dans cet établissement.",
        group="Patients",
        organization_types=_PROVIDER,
    ),
    CapabilityDefinition(
        key=Capability.VISIT_READ,
        label="Voir les visites",
        description="Consulter la liste des visites et l'historique d'une consultation.",
        group="Visites",
        organization_types=_PROVIDER,
    ),
    CapabilityDefinition(
        key=Capability.VISIT_CHECK_IN,
        label="Accueillir un patient",
        description=(
            "Enregistrer l'arrivée d'un patient, voir qui attend, et annuler une arrivée avant le "
            "début de la consultation. Ne donne accès à aucune information clinique."
        ),
        group="Visites",
        organization_types=_PROVIDER,
    ),
    CapabilityDefinition(
        key=Capability.VISIT_DOCUMENT,
        label="Documenter une visite",
        description=(
            "Prendre un patient en consultation, rédiger une note, corriger le motif indiqué et "
            "terminer la visite."
        ),
        group="Visites",
        organization_types=_PROVIDER,
    ),
    CapabilityDefinition(
        key=Capability.ORDER_MANAGE,
        label="Prescrire et suivre des analyses",
        description=(
            "Créer des ordres d'analyse, les orienter vers un laboratoire et suivre leur "
            "avancement."
        ),
        group="Analyses",
        organization_types=_PROVIDER,
    ),
    CapabilityDefinition(
        key=Capability.RESULT_REVIEW,
        label="Réviser un résultat",
        description="Consigner une décision clinique sur un résultat libéré.",
        group="Analyses",
        organization_types=_PROVIDER,
    ),
    CapabilityDefinition(
        key=Capability.LAB_QUEUE_WORK,
        label="Traiter les analyses",
        description=(
            "Accepter ou refuser un ordre, enregistrer le prélèvement, lancer l'analyse et saisir "
            "les résultats."
        ),
        group="Laboratoire",
        organization_types=_LAB,
    ),
    CapabilityDefinition(
        key=Capability.LAB_RESULT_RELEASE,
        label="Libérer un résultat",
        description=(
            "Publier explicitement un résultat vers l'établissement prescripteur. Saisir un "
            "résultat et le libérer restent deux autorisations distinctes."
        ),
        group="Laboratoire",
        organization_types=_LAB,
        grantability=Grantability.PROTECTED_CLINICAL,
    ),
    CapabilityDefinition(
        key=Capability.STAFF_ACCESS_MANAGE,
        label="Gérer l'équipe et les accès",
        description=(
            "Rattacher du personnel, retirer un accès et modifier les autorisations des membres."
        ),
        group="Administration",
        organization_types=_BOTH,
    ),
    CapabilityDefinition(
        key=Capability.FACILITY_CONFIGURE,
        label="Configurer l'établissement",
        description="Gérer le catalogue, les modèles de résultats et la fiche de l'établissement.",
        group="Administration",
        organization_types=_BOTH,
    ),
    CapabilityDefinition(
        key=Capability.BILLING_OPERATE,
        label="Tenir la caisse",
        description=(
            "Établir des factures, encaisser des paiements, consulter les soldes et imprimer les "
            "reçus."
        ),
        group="Facturation",
        organization_types=_BOTH,
    ),
    CapabilityDefinition(
        key=Capability.BILLING_CONFIGURE_PRICES,
        label="Définir les tarifs",
        description="Créer les prestations facturables de l'établissement et fixer leurs tarifs.",
        group="Facturation",
        organization_types=_BOTH,
    ),
)

CAPABILITY_BY_KEY: dict[Capability, CapabilityDefinition] = {
    definition.key: definition for definition in CAPABILITY_CATALOG
}

if len(CAPABILITY_BY_KEY) != len(Capability):  # pragma: no cover - import-time invariant
    _missing = sorted(set(Capability) - set(CAPABILITY_BY_KEY))
    raise RuntimeError(
        # Import-time rather than a test, so a capability added without a French label cannot
        # reach a facility administrator's screen as raw internal vocabulary (§9) -- the
        # application refuses to start instead.
        f"capabilities missing a catalog entry: {_missing}"
    )


#: **The system role templates.** Familiar job titles, expressed as capability bundles (§12).
#:
#: These reproduce exactly the authority each role carried on `790c736`, which is what makes the
#: migration a no-op for every existing member (§18). Read each line as the answer to "what could
#: somebody holding this role actually do, the day before this ticket":
#:
#: * `CLINICIAN` held `require_clinician`, which guarded the patient directory, patient
#:   registration, the whole visit lifecycle, order creation and routing, the order read surface and
#:   doctor review. Every piece of it, and nothing else — notably no billing and no staff
#:   administration, which is why a clinician needing the till is granted it rather than promoted.
#:   It was six capabilities until ADR-069 split `VISIT_CHECK_IN` out of `VISIT_DOCUMENT`; the
#:   template gained the new one the same day, so no clinician's authority changed then either.
#: * `ORG_ADMIN` held `require_org_admin`, which guarded staff memberships *and* facility
#:   configuration, plus `BILLING_CONFIGURE_PRICES` from ADR-066. Deliberately **not**
#:   `BILLING_OPERATE`: administering a facility is not handling its money, and §31 says so.
#: * `LAB_TECH` held `require_lab_tech`. `LAB_RESULT_RELEASER` held `require_lab_result_releaser`.
#:   Neither implies the other — ADR-044, and `require_lab_tech_or_result_releaser` becomes an
#:   any-of question over the two rather than a third role.
#: * `BILLING_CLERK` held `BILLING_OPERATE` and only that (ADR-066 §2).
#:
#: **Immutable in this ticket.** §24 permits deferring editable templates, and this ticket takes
#: that permission: a facility tailors a person through member overrides, which cannot leave an
#: organization without an administrator by surprise the way editing a shared template can. Editable
#: and custom templates are recorded as follow-up work in ADR-067 rather than half-built here.
SYSTEM_ROLE_TEMPLATES: dict[OrganizationRole, frozenset[Capability]] = {
    OrganizationRole.CLINICIAN: frozenset(
        {
            Capability.PATIENT_DIRECTORY_READ,
            Capability.PATIENT_REGISTER,
            Capability.VISIT_READ,
            # DHEALTH-FRONT-DESK-001 §5/ADR-069: compatibility, not authority expansion.
            # Registering an arrival was part of `VISIT_DOCUMENT` until ADR-069 split it out; a
            # clinician who could check a patient in the day before must still be able to.
            Capability.VISIT_CHECK_IN,
            Capability.VISIT_DOCUMENT,
            Capability.ORDER_MANAGE,
            Capability.RESULT_REVIEW,
        }
    ),
    OrganizationRole.ORG_ADMIN: frozenset(
        {
            Capability.STAFF_ACCESS_MANAGE,
            Capability.FACILITY_CONFIGURE,
            Capability.BILLING_CONFIGURE_PRICES,
        }
    ),
    OrganizationRole.LAB_TECH: frozenset({Capability.LAB_QUEUE_WORK}),
    OrganizationRole.LAB_RESULT_RELEASER: frozenset({Capability.LAB_RESULT_RELEASE}),
    OrganizationRole.BILLING_CLERK: frozenset({Capability.BILLING_OPERATE}),
}

if set(SYSTEM_ROLE_TEMPLATES) != set(OrganizationRole):  # pragma: no cover - import-time invariant
    _untemplated = sorted(set(OrganizationRole) - set(SYSTEM_ROLE_TEMPLATES))
    raise RuntimeError(
        # A role with no template would silently strip its holders' authority the moment the
        # resolver replaced the old role checks -- the exact drift §18 calls a P0.
        f"organization roles with no capability template: {_untemplated}"
    )


def check_templates_survive_the_type_filter(
    allowed_roles_by_org_type: dict[OrganizationType, frozenset[OrganizationRole]],
) -> None:
    """Every capability a role's template carries must be holdable at the organization types that
    role may be granted at.

    Import-time, because the failure it catches is silent and has no other symptom.
    `resolve_effective_capabilities` filters its result by organization type, so a template
    carrying a capability the catalog marks provider-only would simply *evaporate* at a laboratory:
    the role grants, the row exists, Team & Access shows it held, and the gate refuses. Authority
    drift with nothing anywhere reporting an error.

    It holds today. Nothing else pins it, and the next capability added to a template is exactly
    when it would stop holding.

    The matrix is passed in rather than imported because `membership.py` owns it and imports *this*
    module -- reaching back for it would be circular. `membership.py` calls this immediately after
    defining the matrix, so the check still runs at import time, which is the point: the
    application refuses to start rather than starting with a capability nobody can hold.
    """
    broken: list[str] = []
    for org_type, roles in allowed_roles_by_org_type.items():
        for role in roles:
            for capability in SYSTEM_ROLE_TEMPLATES[role]:
                if org_type not in CAPABILITY_BY_KEY[capability].organization_types:
                    broken.append(f"{role.value} at {org_type.value} carries {capability.value}")
    if broken:  # pragma: no cover - import-time invariant
        raise RuntimeError(
            "role templates carry capabilities their organization type cannot hold, so they would "
            f"silently evaporate under the resolver's type filter: {sorted(broken)}"
        )


class OverrideEffect(StrEnum):
    """What a member-specific override does. Two values, and no third.

    There is no `INHERIT` and no priority number. A capability a member should simply inherit has no
    override row at all — representing "no opinion" as a row invites two rows disagreeing, and the
    resolver would then need an ordering rule for a case that should not exist.
    """

    GRANT = "GRANT"
    DENY = "DENY"


def capabilities_for_roles(roles: frozenset[str] | set[str]) -> frozenset[Capability]:
    """The union of the capability sets of these system role templates.

    Unknown role names are ignored rather than raising: a role row written by a future migration
    this build does not know about must not take down authorization for everything else the member
    holds. It contributes nothing, which is the safe direction.
    """
    granted: set[Capability] = set()
    for role in roles:
        try:
            template = OrganizationRole(role)
        except ValueError:
            continue
        granted |= SYSTEM_ROLE_TEMPLATES[template]
    return frozenset(granted)


def resolve_effective_capabilities(
    *,
    roles: frozenset[str] | set[str],
    grants: frozenset[str] | set[str] = frozenset(),
    denies: frozenset[str] | set[str] = frozenset(),
    organization_type: OrganizationType | str | None = None,
) -> frozenset[Capability]:
    """**The one resolver.** `(templates ∪ grants) − denies`, then narrowed to what this kind of
    organization may hold at all.

    Every gate in the application funnels through this function (§16). Billing does not compute
    capabilities, the UI does not compute capabilities, Team & Access does not compute capabilities
    — they all ask this, which is what stops two answers to the same question existing.

    ``organization_type`` narrowing is not cosmetic. It is the difference between a stale grant
    being inert and being dangerous: if a capability were granted at a provider and the catalog says
    it is laboratory-only, honouring it here would give authority the write path refuses to
    configure. Filtering at resolution means the answer is the same wherever it is asked.

    **Callers are responsible for membership state.** This function is pure: it is given role names
    and override keys and returns a set. An inactive membership must never reach it with its rows —
    `MembershipCapabilityRepository.effective_for_membership` is what enforces that, in one place,
    against the database.
    """
    effective = set(capabilities_for_roles(roles))

    for key in grants:
        try:
            effective.add(Capability(key))
        except ValueError:
            # A grant row naming a capability this build does not define. Ignored rather than
            # raising, for the same reason an unknown role is: a row from a newer schema must not
            # break authorization for everything else. It cannot grant anything, because no gate
            # asks for a capability that does not exist.
            continue

    for key in denies:
        try:
            effective.discard(Capability(key))
        except ValueError:
            continue

    if organization_type is not None:
        org_type = OrganizationType(organization_type)
        effective = {
            capability
            for capability in effective
            if org_type in CAPABILITY_BY_KEY[capability].organization_types
        }

    return frozenset(effective)


def grantable_capabilities(organization_type: OrganizationType | str) -> frozenset[Capability]:
    """What a facility administrator may grant or deny at this kind of organization.

    The whole catalog, narrowed by organization type. `PROTECTED_CLINICAL` is **not** excluded here,
    and that is a deliberate preservation rather than an oversight: under DHEALTH-NATIVE-ACCESS-001
    an `ORG_ADMIN` could already grant and remove `LAB_RESULT_RELEASER` — the protection ADR-044
    requires is that release never comes *implicitly* with another authority, not that a facility
    cannot appoint its own releaser. §10 is explicit that this ticket must express the existing rule
    rather than broaden or narrow it, so the classification is carried as metadata (for the screen
    to warn on, and for a future policy to key off) without changing who may assign what today.
    """
    org_type = OrganizationType(organization_type)
    return frozenset(
        definition.key
        for definition in CAPABILITY_CATALOG
        if org_type in definition.organization_types
    )


@dataclass(frozen=True, slots=True)
class CapabilityGrantView:
    """One capability, as `Équipe et accès` shows it: held or not, **and why**.

    §20: *"The UI must distinguish effective permission from why the person has it."* An
    administrator about to remove somebody's `CLINICIAN` role needs to know that their billing
    authority came from a direct grant and will survive, or came from the role and will not. Access
    resolution that cannot explain itself gets worked around rather than used.

    `source` is computed **on the server**, beside the resolver that produced `effective`, so the
    browser never re-derives it. A frontend that computed "via which role" would be a second
    implementation of the rule, and the first time the two disagreed the screen would be lying
    about authority.
    """

    key: Capability
    label: str
    description: str
    group: str
    #: Whether the member may actually do this, right now, in this organization.
    effective: bool
    #: `TEMPLATE:<ROLE>` when a role template grants it and no deny overrides it; `GRANT` when an
    #: administrator granted it to this person directly; `DENY` when an administrator took it away
    #: individually; `None` when nothing grants it at all.
    #:
    #: `DENY` is reported even though `effective` is then false, because "refused by an individual
    #: exception" and "never granted" are different facts an administrator acts on differently --
    #: the first is undone by removing the override, the second by granting something.
    source: str | None


def describe_capabilities(
    *,
    roles: frozenset[str] | set[str],
    grants: frozenset[str] | set[str],
    denies: frozenset[str] | set[str],
    organization_type: OrganizationType | str,
    membership_is_active: bool = True,
) -> tuple[CapabilityGrantView, ...]:
    """The whole catalog for this organization type, each entry answered and explained.

    Returns every capability the organization *could* hold, not only the held ones, because Team &
    Access is where an administrator grants what somebody does not yet have -- a list of only the
    held ones would be a screen you cannot use to change anything.

    Uses `resolve_effective_capabilities` for `effective` rather than recomputing it, so the
    explanation can never disagree with the decision it explains."""
    org_type = OrganizationType(organization_type)
    effective = (
        resolve_effective_capabilities(
            roles=roles, grants=grants, denies=denies, organization_type=org_type
        )
        if membership_is_active
        else frozenset()
    )
    template_source: dict[Capability, str] = {}
    for role_name in sorted(roles):
        try:
            role = OrganizationRole(role_name)
        except ValueError:
            continue
        for capability in SYSTEM_ROLE_TEMPLATES[role]:
            template_source.setdefault(capability, f"TEMPLATE:{role.value}")

    views: list[CapabilityGrantView] = []
    for definition in CAPABILITY_CATALOG:
        if org_type not in definition.organization_types:
            continue
        key = definition.key
        if key.value in denies:
            source = "DENY"
        elif key.value in grants:
            source = "GRANT"
        else:
            source = template_source.get(key)
        views.append(
            CapabilityGrantView(
                key=key,
                label=definition.label,
                description=definition.description,
                group=definition.group,
                effective=key in effective,
                source=source,
            )
        )
    return tuple(views)


def parse_capability(value: str) -> Capability:
    """A capability key from an API request, or `ValueError`.

    The gate that makes `POST /capabilities/{anything}` safe (§53). A facility cannot invent a
    permission identifier: if it is not in this enum, no gate asks for it and this refuses to store
    it. `PLATFORM_ADMIN` fails here the same way it fails in
    `staff_access._require_organization_role` — not because a list excludes it, but because it is
    not a member of the type at all.
    """
    return Capability(value)
