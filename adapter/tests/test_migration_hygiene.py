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
