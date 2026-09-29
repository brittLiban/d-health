"""DH-CONNECT-AGENT-001 §68 — the guard that would have caught ``0028``.

Alembic stores the applied revision in ``alembic_version.version_num``, which its own
``op.create_table`` declares as ``VARCHAR(32)``. A longer revision id applies its DDL happily and
then fails to be *stamped*, so the migration cannot complete and the database is left mid-upgrade.

DH-CONNECT-CONTRACT-001's correction shipped a revision named
``0028_connect_qualification_evidence`` — 35 characters. The entire SQLite test suite passed,
because SQLite does not enforce ``VARCHAR`` length at all; real PostgreSQL refused it with
``StringDataRightTruncation`` on the first attempt to apply it. The bug was invisible to every
automated check this repository had.

So the check is here, at the only layer that can see it without a PostgreSQL instance: read the
revision identifiers as text and measure them. The ticket's instruction was *"do not rely on
someone remembering this"* — remembering is precisely what failed the first time.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

#: Alembic's own bootstrap DDL. Not a value this repository chose, so it is not a value this
#: repository may raise: widening it would mean editing a table Alembic creates itself.
ALEMBIC_VERSION_NUM_LENGTH = 32

_VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"


def _revision_assignments(path: Path) -> dict[str, str | None]:
    """Read ``revision`` / ``down_revision`` without importing the module.

    Parsed rather than imported because importing every migration pulls in the whole model layer
    for a check that only needs two string literals, and because a migration that fails to import
    should fail the migration tests, not this one.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: dict[str, str | None] = {}
    for node in tree.body:
        # Both forms, because the tree contains both: Alembic's generated template writes
        # ``revision: str = "..."`` (an AnnAssign) and a hand-written one may drop the annotation.
        if isinstance(node, ast.AnnAssign):
            targets: list[ast.expr] = [node.target]
        elif isinstance(node, ast.Assign):
            targets = list(node.targets)
        else:
            continue
        for target in targets:
            if not isinstance(target, ast.Name) or target.id not in {
                "revision",
                "down_revision",
            }:
                continue
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                found[target.id] = value.value
            elif isinstance(value, ast.Constant) and value.value is None:
                found[target.id] = None
    return found


def _migration_files() -> list[Path]:
    return sorted(p for p in _VERSIONS.glob("*.py") if p.name != "__init__.py")


def test_migration_files_are_discoverable() -> None:
    """Guard the guard: a glob that silently matches nothing proves nothing."""
    files = _migration_files()
    assert len(files) >= 28, f"expected the full migration chain, found {len(files)}"


@pytest.mark.parametrize("path", _migration_files(), ids=lambda p: p.name)
def test_revision_identifiers_fit_alembic_version_num(path: Path) -> None:
    """Every revision id, and every ``down_revision`` that names one, fits ``VARCHAR(32)``.

    ``down_revision`` is checked too. It is written by hand from the previous file's ``revision``,
    and a typo that lengthens it produces a chain that cannot be walked on PostgreSQL for the same
    reason.
    """
    assignments = _revision_assignments(path)
    assert "revision" in assignments, f"{path.name} declares no revision identifier"

    revision = assignments["revision"]
    assert revision is not None, f"{path.name} has a null revision identifier"
    assert len(revision) <= ALEMBIC_VERSION_NUM_LENGTH, (
        f"{path.name}: revision id {revision!r} is {len(revision)} characters; "
        f"alembic_version.version_num is VARCHAR({ALEMBIC_VERSION_NUM_LENGTH}). "
        "PostgreSQL applies the DDL and then refuses to stamp it -- SQLite cannot see this."
    )

    down = assignments.get("down_revision")
    if down is not None:
        assert len(down) <= ALEMBIC_VERSION_NUM_LENGTH, (
            f"{path.name}: down_revision {down!r} is {len(down)} characters, "
            f"over VARCHAR({ALEMBIC_VERSION_NUM_LENGTH})"
        )


def test_the_length_check_would_have_failed_on_the_original_0028_name() -> None:
    """The name that actually shipped and had to be renamed, pinned as a fixture.

    Without this the assertion above is only as good as the assumption that 35 > 32, and a future
    edit that compares the wrong string would still pass on every file in the tree.
    """
    original = "0028_connect_qualification_evidence"
    assert len(original) == 35
    assert len(original) > ALEMBIC_VERSION_NUM_LENGTH

    shipped = _revision_assignments(_VERSIONS / "0028_connect_evidence.py")
    assert shipped["revision"] == "0028_connect_evidence"
    assert len(shipped["revision"] or "") <= ALEMBIC_VERSION_NUM_LENGTH


# ── DHEALTH-LAB-BLOCKED-001: the same lesson, one identifier layer down ──────────────────────
#
# The revision-id check above catches a name too long for `alembic_version.version_num`. It does
# not catch a *constraint* or *index* name too long for PostgreSQL itself, which is a distinct
# failure with an identical signature: SQLite enforces no identifier limit, so the whole unit
# suite stays green while the first real `alembic upgrade` fails outright.
#
# This was not hypothetical. `ck_exchange_lab_order_blocks_resolution_note_requires_resolution`
# was written, is 64 characters, and would have failed on the first PostgreSQL run.

#: PostgreSQL's `NAMEDATALEN - 1`. Not a value this repository chose, and not one it may raise.
POSTGRES_IDENTIFIER_LENGTH = 63


def _explicit_identifier_names(path: Path) -> list[tuple[str, str]]:
    """Every explicitly-written constraint/index name in a migration, as ``(kind, name)``.

    Parsed rather than imported, for the same reason ``_revision_assignments`` is: importing a
    migration pulls in the whole model layer for a check that only needs string literals.

    Reads `name=` keyword arguments and the leading positional name that `op.create_index`,
    `op.create_check_constraint` and `op.create_unique_constraint` take. Auto-generated names
    (SQLAlchemy naming conventions) are deliberately out of scope -- this checks what a human
    typed, which is what a human can get wrong.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[str, str]] = []
    positional_first_name = {
        "create_index",
        "create_check_constraint",
        "create_unique_constraint",
        "create_foreign_key",
        "create_primary_key",
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        attribute = func.attr if isinstance(func, ast.Attribute) else None
        if attribute in positional_first_name and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                found.append((attribute, first.value))
        for keyword in node.keywords:
            if keyword.arg != "name":
                continue
            if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                label = attribute or "constraint"
                found.append((label, keyword.value.value))
    return found


@pytest.mark.parametrize("path", _migration_files(), ids=lambda p: p.name)
def test_explicit_identifier_names_fit_postgres(path: Path) -> None:
    for kind, name in _explicit_identifier_names(path):
        assert len(name) <= POSTGRES_IDENTIFIER_LENGTH, (
            f"{path.name}: {kind} name {name!r} is {len(name)} characters; PostgreSQL's limit is "
            f"{POSTGRES_IDENTIFIER_LENGTH}. SQLAlchemy's PostgreSQL dialect refuses an over-long "
            "explicit identifier, so this migration fails on the first real upgrade -- SQLite, "
            "which has no identifier limit, cannot see it."
        )


def test_the_identifier_check_would_have_failed_on_the_name_that_was_written_first() -> None:
    """The over-long name DHEALTH-LAB-BLOCKED-001 actually wrote, pinned as a fixture.

    Without this, the assertion above is only as good as the assumption that some real name was
    ever close to the limit -- and a future edit that measured the wrong string would still pass
    on every file in the tree. Same reasoning as the 0028 fixture above.
    """
    original = "ck_exchange_lab_order_blocks_resolution_note_requires_resolution"
    assert len(original) == 64
    assert len(original) > POSTGRES_IDENTIFIER_LENGTH

    names = {
        name for _kind, name in _explicit_identifier_names(_VERSIONS / "0037_lab_order_blocks.py")
    }
    assert "ck_exchange_lab_order_blocks_note_requires_resolution" in names
    assert original not in names
