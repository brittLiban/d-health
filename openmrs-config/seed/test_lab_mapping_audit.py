import sys
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

from lab_mapping_audit import (  # noqa: E402
    AmbiguousActiveLoincMatch,
    NoExactActiveLoincMatch,
    OpenElisTest,
    OpenMrsCandidate,
    compare_exact_loinc,
    load_committed_inputs,
    unique_active_loinc,
)


class ExactLoincComparisonTests(unittest.TestCase):
    def test_only_literal_loinc_equality_matches(self) -> None:
        source = (OpenMrsCandidate("source name", "123-4", "source-uuid"),)
        targets = (
            OpenElisTest("same name", "1234", True),
            OpenElisTest("different name", "123-4", True),
        )
        report = compare_exact_loinc(source, targets)
        self.assertEqual(report.exact, ((source[0], targets[1]),))

    def test_zero_match_fails_closed(self) -> None:
        with self.assertRaises(NoExactActiveLoincMatch):
            unique_active_loinc("123-4", (OpenElisTest("inactive", "123-4", False),))

    def test_multiple_active_exact_matches_fail_closed(self) -> None:
        targets = (
            OpenElisTest("one", "123-4", True),
            OpenElisTest("two", "123-4", True),
        )
        with self.assertRaises(AmbiguousActiveLoincMatch):
            unique_active_loinc("123-4", targets)

    def test_ambiguous_code_is_not_silently_in_exact_group(self) -> None:
        source = (OpenMrsCandidate("candidate", "123-4", "source-uuid"),)
        targets = (
            OpenElisTest("one", "123-4", True),
            OpenElisTest("two", "123-4", True),
        )
        report = compare_exact_loinc(source, targets)
        self.assertEqual(report.exact, ())
        self.assertEqual(report.ambiguous, ((source[0], targets),))

    def test_one_active_plus_inactive_is_not_ambiguous(self) -> None:
        active = OpenElisTest("active", "123-4", True)
        self.assertEqual(
            unique_active_loinc("123-4", (active, OpenElisTest("inactive", "123-4", False))),
            active,
        )


class CommittedInventoryTests(unittest.TestCase):
    def test_committed_openmrs_source_remeasures_37_and_21(self) -> None:
        candidates, _ = load_committed_inputs(REPO_ROOT)
        self.assertEqual(len(candidates), 37)
        self.assertEqual(sum(candidate.loinc is not None for candidate in candidates), 21)

    def test_native_openelis_inventory_remeasures_189_total_177_active_and_33_coded(self) -> None:
        _, tests = load_committed_inputs(REPO_ROOT)
        self.assertEqual(len(tests), 189)
        self.assertEqual(sum(test.active for test in tests), 177)
        self.assertEqual(sum(test.active and test.loinc is not None for test in tests), 33)

    def test_committed_active_duplicate_loincs_are_known_and_fail_closed(self) -> None:
        _, tests = load_committed_inputs(REPO_ROOT)
        counts: dict[str, int] = {}
        for test in tests:
            if test.active and test.loinc is not None:
                counts[test.loinc] = counts.get(test.loinc, 0) + 1
        self.assertEqual(
            {loinc for loinc, count in counts.items() if count > 1},
            {"10351-5", "11011-4", "29615-2", "731-0", "7855-0", "94500-6", "94547-7"},
        )
        with self.assertRaises(AmbiguousActiveLoincMatch):
            unique_active_loinc("731-0", tests)

    def test_current_candidate_comparison_has_no_exact_match(self) -> None:
        candidates, tests = load_committed_inputs(REPO_ROOT)
        report = compare_exact_loinc(candidates, tests)
        self.assertEqual(report.exact, ())
        self.assertEqual(report.ambiguous, ())
        self.assertEqual(len(report.openmrs_without_match), 21)
        self.assertEqual(len(report.openmrs_without_loinc), 16)


if __name__ == "__main__":
    unittest.main()
