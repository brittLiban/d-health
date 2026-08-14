#!/usr/bin/env python3
"""Refuse any OpenMRS form schema that performs clinical interpretation (ADR-006, DH-004).

ADR-006 is a pinned P0: the partograph ships as DATA ENTRY ONLY — no alert line, no
action line, no prolonged-labour flagging. AGENTS.md adds that a near deadline is not
implicit permission to add it "helpfully".

Until now that constraint lived only in prose, which means it was enforced by whoever
happened to remember it. This makes it executable: a schema that computes, scores,
warns, or flags fails the check, and the deployment stops.

The check is deliberately BLUNT and errs toward refusing. A false refusal costs a
conversation; a false pass puts decision support in front of a midwife in a system whose
entire premise is that it records and routes rather than interprets.

Usage:
    validate_form_safety.py PATH [PATH ...]      # files or directories of *.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Schema keys that make a form compute or judge rather than record. AMPATH form engine
# evaluates these; any of them turns recorded data into derived clinical output.
FORBIDDEN_KEYS = {
    "calculate": "computes a derived value from what the clinician entered",
    "calculateexpression": "computes a derived value from what the clinician entered",
    "alert": "raises a clinical alert",
    "alerts": "raises a clinical alert",
    "warning": "raises a clinical warning",
    "warnings": "raises a clinical warning",
}

# Vocabulary that names decision support directly. Matched case-insensitively against
# labels, ids, and concept names anywhere in the schema.
FORBIDDEN_PHRASES = {
    "alert line": "ADR-006 excludes the partograph alert line",
    "action line": "ADR-006 excludes the partograph action line",
    "prolonged labour": "ADR-006 excludes prolonged-labour flagging",
    "prolonged labor": "ADR-006 excludes prolonged-labour flagging",
    "risk score": "scoring is clinical interpretation",
    "risk category": "categorising risk is clinical interpretation",
    "triage": "triage is clinical interpretation",
    "recommend": "recommending action is clinical interpretation",
    "refer immediately": "recommending action is clinical interpretation",
    "danger sign": "flagging danger signs is clinical interpretation",
}


class FormSafetyError(RuntimeError):
    """A form schema contains clinical interpretation and must not be deployed."""


def _walk(node: Any, path: str = "$"):
    """Yield (json_path, key, value) for every key in the document."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield path, key, value
            yield from _walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, f"{path}[{index}]")


def check_schema(schema: dict[str, Any], *, source: str) -> list[str]:
    """Return every ADR-006 violation found in one parsed form schema."""
    violations: list[str] = []

    for path, key, value in _walk(schema):
        if key.lower() in FORBIDDEN_KEYS:
            violations.append(f"{source}: {path}.{key} — {FORBIDDEN_KEYS[key.lower()]} (ADR-006)")
        # A validator whose type is a JS expression is arbitrary computed logic; it is
        # how conditional clinical warnings get added without a key named "alert".
        if key == "type" and isinstance(value, str) and value.lower() == "js_expression":
            violations.append(
                f"{source}: {path}.{key} — js_expression validator is arbitrary "
                f"computed logic (ADR-006)"
            )

    haystack = json.dumps(schema, ensure_ascii=False).lower()
    for phrase, why in FORBIDDEN_PHRASES.items():
        if phrase in haystack:
            violations.append(f"{source}: contains {phrase!r} — {why} (ADR-006)")

    return violations


def iter_schema_files(paths: list[str]) -> list[Path]:
    """Resolve inputs to schema files.

    ⚠️ Finding nothing is an ERROR, not a pass. A checker that reports success having
    examined zero files is worse than no checker: it produces a green result that
    someone will later cite as evidence. This was a real defect in this very script —
    pointed at a directory whose contents were not visible to it, it printed
    "no clinical interpretation found" and exited 0.
    """
    files: list[Path] = []
    for raw in paths:
        candidate = Path(raw)
        if candidate.is_dir():
            found = sorted(candidate.rglob("*.json"))
            if not found:
                raise FormSafetyError(
                    f"{raw} contains no *.json schema — refusing to report a pass for "
                    f"an empty check. If this directory is genuinely empty, do not run "
                    f"the guard against it."
                )
            files.extend(found)
        elif candidate.is_file():
            files.append(candidate)
        else:
            raise FormSafetyError(f"{raw} is neither a file nor a directory")
    if not files:
        raise FormSafetyError("no schema files resolved from the given paths")
    return files


def validate(paths: list[str]) -> list[str]:
    violations: list[str] = []
    for file in iter_schema_files(paths):
        try:
            schema = json.loads(file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise FormSafetyError(f"{file}: not valid JSON — {exc}") from exc
        if not isinstance(schema, dict):
            raise FormSafetyError(f"{file}: form schema must be a JSON object")
        violations.extend(check_schema(schema, source=str(file)))
    return violations


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        violations = validate(argv)
    except FormSafetyError as exc:
        print(f"form safety check failed: {exc}", file=sys.stderr)
        return 1

    if violations:
        print("ADR-006 VIOLATION — this form performs clinical interpretation:", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation}", file=sys.stderr)
        print(
            "\nThis system records and routes; it does not interpret. Removing the "
            "constraint requires a superseding ADR, not an edit here.",
            file=sys.stderr,
        )
        return 1

    checked = len(iter_schema_files(argv))
    print(f"form safety: {checked} schema(s) checked, no clinical interpretation found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
