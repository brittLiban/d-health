"""Tests for the ADR-006 form safety guard (DH-004).

The guard exists because "the partograph is data entry only" was a sentence in a
document. These tests are what make it a property of the deployment: each one encodes
a specific way decision support gets added to a form, and asserts it is refused.
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("validate_form_safety.py")
SPEC = importlib.util.spec_from_file_location("validate_form_safety", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Unable to load validate_form_safety.py")
guard = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = guard
SPEC.loader.exec_module(guard)


def question(**options):
    """A minimal AMPATH obs question with the given questionOptions."""
    return {
        "name": "Partograph",
        "pages": [
            {
                "label": "Labour",
                "sections": [
                    {
                        "label": "Observations",
                        "questions": [
                            {
                                "label": "Cervical dilatation",
                                "type": "obs",
                                "id": "cervicalDilatation",
                                "questionOptions": {"rendering": "number", **options},
                            }
                        ],
                    }
                ],
            }
        ],
    }


class AcceptsPlainDataEntry(unittest.TestCase):
    def test_plain_observation_form_passes(self):
        schema = question(concept="abc-123")
        self.assertEqual(guard.check_schema(schema, source="t"), [])

    def test_recording_a_measurement_is_not_interpretation(self):
        """Recording cervical dilatation over time is the partograph. That is allowed."""
        schema = question(concept="abc-123", answers=[])
        schema["pages"][0]["sections"][0]["questions"].append(
            {
                "label": "Fetal heart rate",
                "type": "obs",
                "id": "fetalHeartRate",
                "questionOptions": {"rendering": "number", "concept": "def-456"},
            }
        )
        self.assertEqual(guard.check_schema(schema, source="t"), [])


class RefusesClinicalInterpretation(unittest.TestCase):
    def test_alert_line_is_refused(self):
        schema = question(concept="abc")
        schema["pages"][0]["sections"][0]["label"] = "Alert line"
        violations = guard.check_schema(schema, source="t")
        self.assertTrue(any("alert line" in v.lower() for v in violations))

    def test_action_line_is_refused(self):
        schema = question(concept="abc")
        schema["pages"][0]["label"] = "Action Line crossing"
        violations = guard.check_schema(schema, source="t")
        self.assertTrue(any("action line" in v.lower() for v in violations))

    def test_prolonged_labour_flagging_is_refused(self):
        schema = question(concept="abc")
        schema["description"] = "Flags prolonged labour automatically"
        violations = guard.check_schema(schema, source="t")
        self.assertTrue(any("prolonged labour" in v.lower() for v in violations))

    def test_calculated_field_is_refused(self):
        """A computed value is derived clinical output, whatever it is labelled."""
        schema = question(concept="abc", calculate={"calculateExpression": "x*2"})
        violations = guard.check_schema(schema, source="t")
        self.assertTrue(any("calculate" in v.lower() for v in violations))

    def test_js_expression_validator_is_refused(self):
        """The way conditional warnings get added without a key called 'alert'."""
        schema = question(concept="abc")
        schema["pages"][0]["sections"][0]["questions"][0]["validators"] = [
            {"type": "js_expression", "failsWhenExpression": "myValue > 10"}
        ]
        violations = guard.check_schema(schema, source="t")
        self.assertTrue(any("js_expression" in v for v in violations))

    def test_risk_score_is_refused(self):
        schema = question(concept="abc")
        schema["pages"][0]["sections"][0]["questions"][0]["label"] = "Obstetric risk score"
        violations = guard.check_schema(schema, source="t")
        self.assertTrue(any("risk score" in v.lower() for v in violations))

    def test_danger_sign_flagging_is_refused(self):
        schema = question(concept="abc")
        schema["pages"][0]["sections"][0]["label"] = "Danger signs"
        violations = guard.check_schema(schema, source="t")
        self.assertTrue(any("danger sign" in v.lower() for v in violations))

    def test_nested_violation_is_still_found(self):
        """Burying it deeper in the schema must not evade the check."""
        schema = question(concept="abc")
        schema["pages"][0]["sections"][0]["questions"][0]["questionOptions"]["answers"] = [
            {"label": "Recommend caesarean", "concept": "xyz"}
        ]
        violations = guard.check_schema(schema, source="t")
        self.assertTrue(any("recommend" in v.lower() for v in violations))


class FileHandling(unittest.TestCase):
    def test_directory_of_schemas_is_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "form.json"
            path.write_text(json.dumps(question(concept="abc")), encoding="utf-8")
            self.assertEqual(guard.validate([directory]), [])

    def test_invalid_json_fails_loudly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(guard.FormSafetyError):
                guard.validate([directory])

    def test_missing_path_fails_loudly(self):
        with self.assertRaises(guard.FormSafetyError):
            guard.validate(["/nonexistent/place"])

    def test_exit_code_is_nonzero_when_a_violation_is_found(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            schema = question(concept="abc")
            schema["description"] = "Draws the alert line"
            path.write_text(json.dumps(schema), encoding="utf-8")
            self.assertEqual(guard.main([str(path)]), 1)

    def test_exit_code_is_zero_for_a_clean_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ok.json"
            path.write_text(json.dumps(question(concept="abc")), encoding="utf-8")
            self.assertEqual(guard.main([str(path)]), 0)


class RefusesToPassOnAnEmptyCheck(unittest.TestCase):
    """A checker that examines zero files must NOT report success.

    This was a real defect here: pointed at a directory whose contents it could not
    see, the guard printed "no clinical interpretation found" and exited 0. That green
    result would later have been cited as evidence the forms were safe.
    """

    def test_directory_with_no_schemas_is_an_error_not_a_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(guard.FormSafetyError) as caught:
                guard.validate([directory])
            self.assertIn("refusing to report a pass", str(caught.exception))

    def test_empty_check_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(guard.main([directory]), 1)

    def test_no_paths_at_all_exits_nonzero(self):
        self.assertEqual(guard.main([]), 2)


class GuardsTheRealDeployment(unittest.TestCase):
    def test_every_form_schema_in_the_repo_passes(self):
        """Whatever forms this repo ships must satisfy ADR-006, now and later.

        The repo ships no form schemas yet — DH-004's fields need clinical sign-off.
        When the first one lands in this directory, this test starts guarding it
        automatically rather than needing to be remembered.
        """
        schemas = sorted(MODULE_PATH.parent.rglob("*.json"))
        if not schemas:
            self.skipTest("no form schemas committed yet (DH-004 awaits sign-off)")
        self.assertEqual(guard.validate([str(p) for p in schemas]), [])


if __name__ == "__main__":
    unittest.main()
