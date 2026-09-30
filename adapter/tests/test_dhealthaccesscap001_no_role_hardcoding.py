"""DHEALTH-ACCESS-CAPABILITIES-001 §43: role names are not authority, structurally.

DHEALTH-BILLING-001 proved this for one module with an AST-based guard. This generalizes it: the
business and navigation surfaces named below must not decide authorization from a staff job title,
and the check is a parse rather than a grep so prose explaining the model stays legal while code
depending on it does not.

── The allowlist is small, and every entry is justified here ──

Role names may appear in:

* **`capabilities.py`** — it owns `SYSTEM_ROLE_TEMPLATES`, the one place a role becomes a set of
  capabilities. That mapping is the whole point; forbidding it here would forbid the feature.
* **`membership.py`** — the role matrix (`ALLOWED_ROLES_BY_ORG_TYPE`) and the assignment rows.
* **`staff_access.py`** — granting and removing role *templates* is what this module does.
* **`api.py`** — the role endpoints Team & Access calls, and the `/me` response that reports a
  person's job titles as labels.
* **`models.py`** — the enum and the CHECK constraints.
* **`demo_seed.py`** — the demo baseline assigns roles, exactly as a facility would.
* **tests and fixtures** — a test standing in for the server must be able to say "a clinician".

Everywhere else, a role name in code is a defect: it means that module decides authority from a job
title, which is precisely what this ticket removed and what NC-CAP-1 re-introduces to prove the
guard bites.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from daher_adapter.exchange.capabilities import CAPABILITY_CATALOG, Capability

_ADAPTER = Path(__file__).resolve().parents[1]
_EXCHANGE = _ADAPTER / "src" / "daher_adapter" / "exchange"
_UI = _ADAPTER.parent / "ui" / "src"

#: Every role name that must not appear as authorization logic outside the access subsystem.
ROLE_NAMES = frozenset(
    {
        "ORG_ADMIN",
        "CLINICIAN",
        "LAB_TECH",
        "LAB_RESULT_RELEASER",
        "BILLING_CLERK",
        "OrganizationRole",
    }
)

#: The access subsystem, plus the two places a role is legitimately *data*. See the module
#: docstring for why each one is here; the list is deliberately short and adding to it is a
#: decision, not a convenience.
PYTHON_ALLOWED = frozenset(
    {
        "capabilities.py",
        "membership.py",
        "staff_access.py",
        "authorization.py",
        "api.py",
        "models.py",
        "demo_seed.py",
    }
)

#: The Python modules this rule is *about*: the domain services that do the facility's actual work.
#: Named explicitly rather than "everything not allowlisted", so a new module is considered
#: deliberately rather than silently exempted by being forgotten.
PYTHON_GUARDED = (
    "billing.py",
    "visits.py",
    "results.py",
    "lab_workflow.py",
    "order_intents.py",
    "patients.py",
    "selection.py",
    "catalog.py",
    "directory.py",
)


def _code_identifiers_and_strings(path: Path) -> set[str]:
    """Names, attributes and *expression* strings — never docstrings or comments.

    The distinction is the whole reason this is an AST walk. A module explaining *why* a cashier is
    not granted `CLINICIAN` is doing exactly what this codebase asks of it; a module comparing
    against `CLINICIAN` is the defect. A grep cannot tell those apart, and a grep-based version of
    this test would have to be weakened until it caught nothing.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        ast.get_docstring(node, clean=False)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
    }
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value not in docstrings:
                found.add(node.value)
    return found


def test_no_domain_service_decides_authority_from_a_role_name():
    """The central structural guarantee, and what NC-CAP-1 breaks.

    Each guarded module is parsed and every identifier, attribute and expression string is checked
    against the role vocabulary. A module that mentions a role only in prose passes; one that
    compares against it does not."""
    offences: list[str] = []
    for name in PYTHON_GUARDED:
        path = _EXCHANGE / name
        if not path.exists():  # pragma: no cover - the module list outliving a rename
            continue
        assert name not in PYTHON_ALLOWED, f"{name} cannot be both guarded and allowed"
        for identifier in sorted(_code_identifiers_and_strings(path) & ROLE_NAMES):
            offences.append(f"  {name}: {identifier!r} used in code")

    assert not offences, (
        "role names are being used as authority in business code (§17/§43). Ask for a capability "
        "instead:\n" + "\n".join(offences)
    )


def test_the_allowlist_stays_small_and_every_entry_is_real():
    """An allowlist nobody maintains becomes the whole codebase.

    Two things: every allowed file exists (so a rename does not silently widen the exemption), and
    the list has not grown past the access subsystem plus the three data-owning modules."""
    for name in PYTHON_ALLOWED:
        assert (_EXCHANGE / name).exists(), f"allowlisted file {name} no longer exists"
    assert len(PYTHON_ALLOWED) <= 8, (
        "the anti-hardcoding allowlist is growing. Each addition means one more module deciding "
        "authority from a job title -- which is the thing this ticket removed."
    )


# -- the frontend --------------------------------------------------------------------------------

#: Frontend files that must not authorize on role names. Navigation and the capability module
#: above all: those are where "shows the wrong person the wrong screen" lives.
TS_GUARDED = (
    "features/staff/staffSections.ts",
    "features/workspace/workspaceCapabilities.ts",
    "features/billing/BillingPage.tsx",
    "features/lab/LabProvider.tsx",
    # **Was `ClinicContext.tsx` until DHEALTH-FRONT-DESK-001 found the typo.** The file is, and
    # always was, `.ts` -- so this entry matched nothing and `continue`d silently: an
    # allowlist-shaped hole in a guard, which is worse than an absent guard because it reads as
    # covered. `test_every_guarded_frontend_file_exists` below makes that class of typo impossible.
    "features/clinic/ClinicContext.ts",
    # DHEALTH-FRONT-DESK-001/ADR-069 §60. The arrival surface and the screen that hosts it: where
    # "shows the wrong person the wrong control" would live for the newest workflow, and where
    # NC-FD-8 re-introduces a role check to prove the guard bites.
    "features/visits/ClinicArrivals.tsx",
    "features/visits/ClinicVisitsPage.tsx",
    "features/clinic/pages/ClinicHomePage.tsx",
    # DHEALTH-LAB-RECEPTION-001. The laboratory's own intake surface and the screen that counts the
    # work waiting on it. `features/lab/LabProvider.tsx` was already here, which covered how the
    # laboratory context is *derived* but not the two screens that decide which controls a worker is
    # offered -- so "shows the wrong person the wrong control" had no guard on the laboratory side at
    # all, the same allowlist-shaped hole DHEALTH-FRONT-DESK-001 found in the `ClinicContext` entry
    # above. NC-LR-12 re-introduces a role check in `LabOrdersPage.tsx` to prove the guard bites.
    "features/lab/pages/LabOrdersPage.tsx",
    "features/lab/pages/LabHomePage.tsx",
)

#: `roles.includes('X')`, `role === 'X'`, `hasRole('X')` -- the shapes §1 names.
_TS_ROLE_LOGIC = re.compile(
    r"""(roles\s*\.\s*(includes|some|indexOf)\s*\(|role\s*===|===\s*['"](ORG_ADMIN|CLINICIAN|"""
    r"""LAB_TECH|LAB_RESULT_RELEASER|BILLING_CLERK)['"]|hasRole\s*\()"""
)


def _strip_ts_comments(source: str) -> str:
    """Block and line comments removed, so prose explaining the model is not a violation.

    A full TypeScript parse would be better and is not worth a new dependency for five files; what
    this must not do is fail on a docstring, and removing comments achieves exactly that. String
    literals survive, which is the direction that matters -- a role name in a live string is what
    the check is looking for."""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"^\s*//.*$", "", source, flags=re.MULTILINE)


def test_no_navigation_or_capability_module_authorizes_on_a_role_name():
    """§43's frontend half, and what NC-CAP-7 breaks.

    `staffSections.ts` is the file that matters most: it decides what a person is shown, and it
    used to do so from role names. If a role comparison reappears there, somebody's screen has
    stopped following their authority."""
    if not _UI.exists():  # pragma: no cover - adapter checked out without the UI
        pytest.skip("the UI tree is not present")

    offences: list[str] = []
    for relative in TS_GUARDED:
        path = _UI / relative
        if not path.exists():  # pragma: no cover
            continue
        body = _strip_ts_comments(path.read_text(encoding="utf-8"))
        for match in _TS_ROLE_LOGIC.finditer(body):
            line = body[: match.start()].count("\n") + 1
            offences.append(f"  {relative}:{line}: {match.group(0).strip()!r}")

    assert not offences, (
        "frontend authorization is reading role names again (§25/§43). Ask "
        "`can(capabilities, Capability.X)`:\n" + "\n".join(offences)
    )


def test_every_guarded_frontend_file_exists():
    """The guard above skips a path it cannot find, so a typo silently exempts a file.

    **This is not hypothetical.** `TS_GUARDED` carried `features/clinic/ClinicContext.tsx` from
    DHEALTH-ACCESS-CAPABILITIES-001 until DHEALTH-FRONT-DESK-001; the real file is `.ts`, so that
    entry matched nothing for the whole intervening period while reading as covered. The Python half
    has had `test_the_allowlist_stays_small_and_every_entry_is_real` doing exactly this job since
    day
    one -- the frontend half simply never got its equivalent.

    Kept as a separate test rather than an assertion inside the guard, so "a guarded file vanished"
    and "a guarded file broke the rule" are two different failures with two different fixes."""
    if not _UI.exists():  # pragma: no cover - the adapter checked out without the UI
        pytest.skip("the UI tree is not present")

    missing = [relative for relative in TS_GUARDED if not (_UI / relative).exists()]
    assert not missing, (
        "guarded frontend files do not exist, so the structural guard silently skips them. "
        f"Fix the path or remove the entry deliberately: {missing}"
    )


# -- the two catalogs agree ------------------------------------------------------------------------


def test_the_frontend_catalog_matches_the_backend_catalog():
    """`workspaceCapabilities.ts`'s `Capability` object names exactly what the server defines.

    A key present in one and not the other is silently broken rather than loudly: a frontend asking
    for a capability the server never sends gets `false` for ever and hides a screen with no error
    anywhere. Extracted from the TypeScript source rather than duplicated here, so this test cannot
    pass by agreeing with a stale copy of itself."""
    if not _UI.exists():  # pragma: no cover
        pytest.skip("the UI tree is not present")

    source = (_UI / "features/workspace/workspaceCapabilities.ts").read_text(encoding="utf-8")
    block = source[source.index("export const Capability = {") : source.index("} as const")]
    frontend = set(re.findall(r":\s*'([a-z._]+)'", block))
    backend = {capability.value for capability in Capability}

    assert frontend == backend, (
        f"catalogs disagree. Only in the frontend: {sorted(frontend - backend)}; "
        f"only in the backend: {sorted(backend - frontend)}"
    )


def test_every_capability_is_described_in_french_for_facility_staff():
    """§9: no facility administrator is shown internal vocabulary.

    Checked here as well as at import time, because the import-time guard only proves a catalog
    *entry* exists -- this proves the entry says something, in French, that a human can act on."""
    for definition in CAPABILITY_CATALOG:
        assert definition.label and definition.label != definition.key.value, (
            f"{definition.key.value} has no human label"
        )
        assert definition.description.endswith("."), (
            f"{definition.key.value}'s description is not a sentence"
        )
        assert definition.group, f"{definition.key.value} has no group heading"
        # A label that is just the key with dots removed would pass the first check and fail a
        # reader; the catalog is French prose and an ASCII-only label is a sign it is not.
        assert not re.fullmatch(r"[a-z._]+", definition.label), (
            f"{definition.key.value}'s label looks like an identifier"
        )


def test_the_authority_matrix_baseline_is_still_present():
    """The §18 baseline is a committed artefact, not something regenerated on demand.

    If this file goes missing the matrix test skips silently and the ticket's central guarantee
    stops being checked -- which is exactly the kind of green nobody notices."""
    baseline = Path(__file__).with_name("dhealthaccesscap001_authority_matrix.json")
    assert baseline.exists(), "the pre-migration authority matrix baseline is missing"
    matrix = json.loads(baseline.read_text(encoding="utf-8"))
    assert len(matrix) >= 16, "the baseline covers fewer boundaries than it did when captured"
    for row in matrix.values():
        assert "outsider" in row and "platform" in row
