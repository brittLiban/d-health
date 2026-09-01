"""Read-only DH-011I exact-LOINC compatibility audit.

This module never chooses clinical content. It parses the committed OpenMRS candidate table and a
native OpenELIS inventory, compares literal LOINC strings, and fails closed whenever an active
target code has zero or more than one match. Names and displays are never matching inputs.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from io import StringIO
from pathlib import Path


@dataclass(frozen=True)
class OpenMrsCandidate:
    name: str
    loinc: str | None
    concept_uuid: str


@dataclass(frozen=True)
class OpenElisTest:
    name: str
    loinc: str | None
    active: bool


@dataclass(frozen=True)
class ExactLoincReport:
    exact: tuple[tuple[OpenMrsCandidate, OpenElisTest], ...]
    ambiguous: tuple[tuple[OpenMrsCandidate, tuple[OpenElisTest, ...]], ...]
    openmrs_without_match: tuple[OpenMrsCandidate, ...]
    openelis_not_referenced: tuple[OpenElisTest, ...]
    openmrs_without_loinc: tuple[OpenMrsCandidate, ...]


class NoExactActiveLoincMatch(LookupError):
    pass


class AmbiguousActiveLoincMatch(LookupError):
    pass


def parse_openmrs_candidates(markdown: str) -> tuple[OpenMrsCandidate, ...]:
    """Parse only the section-2 candidate table in docs/CLINICAL-SIGNOFF.md."""
    in_candidates = False
    candidates: list[OpenMrsCandidate] = []
    for line in markdown.splitlines():
        if line.startswith("## 2. Candidate lab sets"):
            in_candidates = True
            continue
        if in_candidates and line.startswith("## 3."):
            break
        if not in_candidates or not line.startswith("| ☐ |"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 4:
            raise ValueError(f"malformed OpenMRS candidate row: {line}")
        _, name, raw_loinc, concept_uuid = cells
        if name == "Lab set (OpenMRS reference name)":
            continue
        loinc = raw_loinc.strip("`")
        candidates.append(
            OpenMrsCandidate(
                name=name,
                loinc=None if loinc == "—" else loinc,
                concept_uuid=concept_uuid.strip("`"),
            )
        )
    if not candidates:
        raise ValueError("no OpenMRS candidate rows found")
    return tuple(candidates)


def parse_openelis_inventory(csv_text: str) -> tuple[OpenElisTest, ...]:
    rows = csv.DictReader(StringIO(csv_text))
    if rows.fieldnames != ["OpenELIS test name", "LOINC", "active?"]:
        raise ValueError(f"unexpected OpenELIS inventory header: {rows.fieldnames}")
    tests: list[OpenElisTest] = []
    for row in rows:
        raw_active = row["active?"]
        if raw_active not in {"yes", "no"}:
            raise ValueError(f"invalid active value: {raw_active!r}")
        loinc = row["LOINC"].strip()
        tests.append(
            OpenElisTest(
                name=row["OpenELIS test name"],
                loinc=loinc or None,
                active=raw_active == "yes",
            )
        )
    if not tests:
        raise ValueError("no OpenELIS tests found")
    return tuple(tests)


def unique_active_loinc(loinc: str, tests: tuple[OpenElisTest, ...]) -> OpenElisTest:
    """Return one literal active match; zero and 2+ both stop delivery."""
    matches = tuple(test for test in tests if test.active and test.loinc == loinc)
    if not matches:
        raise NoExactActiveLoincMatch(loinc)
    if len(matches) != 1:
        raise AmbiguousActiveLoincMatch(f"{loinc}: {len(matches)} active OpenELIS tests")
    return matches[0]


def compare_exact_loinc(
    candidates: tuple[OpenMrsCandidate, ...], tests: tuple[OpenElisTest, ...]
) -> ExactLoincReport:
    active_by_loinc: dict[str, list[OpenElisTest]] = {}
    for test in tests:
        if test.active and test.loinc is not None:
            active_by_loinc.setdefault(test.loinc, []).append(test)

    exact: list[tuple[OpenMrsCandidate, OpenElisTest]] = []
    ambiguous: list[tuple[OpenMrsCandidate, tuple[OpenElisTest, ...]]] = []
    missing: list[OpenMrsCandidate] = []
    without_loinc: list[OpenMrsCandidate] = []
    referenced_codes: set[str] = set()

    for candidate in candidates:
        if candidate.loinc is None:
            without_loinc.append(candidate)
            continue
        referenced_codes.add(candidate.loinc)
        matches = tuple(active_by_loinc.get(candidate.loinc, ()))
        if len(matches) == 1:
            exact.append((candidate, matches[0]))
        elif len(matches) > 1:
            ambiguous.append((candidate, matches))
        else:
            missing.append(candidate)

    target_only = tuple(
        test
        for test in tests
        if test.active and test.loinc is not None and test.loinc not in referenced_codes
    )
    return ExactLoincReport(
        exact=tuple(exact),
        ambiguous=tuple(ambiguous),
        openmrs_without_match=tuple(missing),
        openelis_not_referenced=target_only,
        openmrs_without_loinc=tuple(without_loinc),
    )


def load_committed_inputs(repo_root: Path) -> tuple[tuple[OpenMrsCandidate, ...], tuple[OpenElisTest, ...]]:
    candidates = parse_openmrs_candidates((repo_root / "docs/CLINICAL-SIGNOFF.md").read_text())
    tests = parse_openelis_inventory(
        (repo_root / "docs/DH-011I-OPENELIS-TEST-INVENTORY.csv").read_text()
    )
    return candidates, tests
