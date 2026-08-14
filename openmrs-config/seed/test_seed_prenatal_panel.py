"""Safety tests for the DH-006 prenatal panel seed.

These assert the properties that protect clinical content: the panel ships empty,
the seed never invents a concept, and it never removes a member that may already
have been used to order tests for a patient.
"""

import csv
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("seed_prenatal_panel.py")
SPEC = importlib.util.spec_from_file_location("seed_prenatal_panel", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Unable to load seed_prenatal_panel.py")
seed = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = seed
SPEC.loader.exec_module(seed)

MEMBERS_FILE = Path(__file__).with_name("prenatal-panel.members.csv")


def _find_signoff_sheet():
    """Locate CLINICAL-SIGNOFF.md from the repo tree or the test container's mount."""
    here = Path(__file__).resolve()
    candidates = [parent / "docs" / "CLINICAL-SIGNOFF.md" for parent in here.parents]
    candidates.append(Path("/docs/CLINICAL-SIGNOFF.md"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError("CLINICAL-SIGNOFF.md not found; DH-006 requires it to exist")


SIGNOFF_SHEET = _find_signoff_sheet()


class FakeAPI:
    """Records calls instead of issuing them."""

    def __init__(self, order_set=None, known_concepts=()):
        self._order_set = order_set
        self._known = set(known_concepts)
        self.created = []

    def get(self, resource, query=None):
        if resource.startswith("concept/"):
            uuid = resource.split("/", 1)[1]
            if uuid not in self._known:
                raise seed.SeedError(f"not found: {uuid}")
            return {"uuid": uuid, "display": uuid}
        if resource.startswith("orderset/"):
            if self._order_set is None:
                raise seed.SeedError("not found")
            return self._order_set
        raise AssertionError(resource)

    def create(self, resource, body):
        self.created.append((resource, body))
        if resource == "orderset":
            self._order_set = {"uuid": seed.ORDER_SET_UUID, "orderSetMembers": []}
        return {"uuid": "created"}


class CommittedStateTests(unittest.TestCase):
    """The panel must ship empty, and must keep saying why."""

    def test_committed_members_file_is_empty(self):
        self.assertEqual(
            seed.read_members(str(MEMBERS_FILE)),
            [],
            "prenatal-panel.members.csv must stay empty until the clinical lead "
            "signs off (AGENTS.md: never invent clinical content)",
        )

    def test_committed_members_file_keeps_its_warning(self):
        text = MEMBERS_FILE.read_text(encoding="utf-8")
        self.assertIn("SHIPS EMPTY ON PURPOSE", text)
        self.assertIn("clinical lead", text)

    def test_members_file_parses_as_csv_after_comments_are_stripped(self):
        rows = [
            line
            for line in MEMBERS_FILE.read_text(encoding="utf-8").splitlines(True)
            if not line.lstrip().startswith("#")
        ]
        self.assertEqual(csv.DictReader(rows).fieldnames, ["concept_uuid", "label"])

    def test_sign_off_sheet_recommends_nothing(self):
        text = SIGNOFF_SHEET.read_text(encoding="utf-8")
        # Strip markdown emphasis so this tracks the wording, not the formatting.
        plain = text.replace("*", "").replace("_", "").lower()
        self.assertIn("not a recommendation", plain)
        self.assertIn("no test below is proposed", plain)
        self.assertNotIn("[x]", plain, "nothing may be pre-selected for the lead")
        self.assertNotIn("☑", text, "nothing may be pre-selected for the lead")


class ReadMembersTests(unittest.TestCase):
    def test_missing_file_is_valid_not_an_error(self):
        self.assertEqual(seed.read_members("/nonexistent/path.csv"), [])

    def test_duplicate_member_is_rejected(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as handle:
            handle.write("concept_uuid,label\nabc,One\nabc,Again\n")
            path = handle.name
        with self.assertRaises(seed.SeedError):
            seed.read_members(path)


class SeedSafetyTests(unittest.TestCase):
    def test_unknown_concept_stops_the_seed(self):
        """The seed must never create a clinical concept that does not exist."""
        api = FakeAPI()
        with self.assertRaises(seed.SeedError) as caught:
            seed.seed(api, [seed.PanelMember("missing-uuid", "Some test")])
        self.assertIn("does not invent clinical concepts", str(caught.exception))
        self.assertEqual(api.created, [], "nothing may be created for an unknown concept")

    def test_empty_panel_creates_the_order_set_with_no_members(self):
        api = FakeAPI()
        added = seed.seed(api, [])
        self.assertEqual(added, 0)
        self.assertEqual(len(api.created), 1)
        resource, body = api.created[0]
        self.assertEqual(resource, "orderset")
        self.assertEqual(body["orderSetMembers"], [])

    def test_existing_members_are_never_removed(self):
        """A member present in OpenMRS but absent from the file is left alone."""
        order_set = {
            "uuid": seed.ORDER_SET_UUID,
            "orderSetMembers": [{"concept": {"uuid": "already-there"}, "retired": False}],
        }
        api = FakeAPI(order_set=order_set, known_concepts=("wanted",))
        seed.seed(api, [seed.PanelMember("wanted", "Wanted test")])

        self.assertTrue(any("ordersetmember" in r for r, _ in api.created))
        for resource, body in api.created:
            self.assertNotIn("retire", resource.lower())
            self.assertIsNot(body.get("retired"), True)

    def test_already_present_member_is_not_added_twice(self):
        order_set = {
            "uuid": seed.ORDER_SET_UUID,
            "orderSetMembers": [{"concept": {"uuid": "wanted"}, "retired": False}],
        }
        api = FakeAPI(order_set=order_set, known_concepts=("wanted",))
        added = seed.seed(api, [seed.PanelMember("wanted", "Wanted test")])
        self.assertEqual(added, 0)
        self.assertEqual(api.created, [])

    def test_retired_members_are_not_counted_as_present(self):
        order_set = {
            "uuid": seed.ORDER_SET_UUID,
            "orderSetMembers": [{"concept": {"uuid": "gone"}, "retired": True}],
        }
        self.assertEqual(seed.existing_member_concepts(order_set), set())


if __name__ == "__main__":
    unittest.main()
