import csv
import unittest
from pathlib import Path


CSV_PATH = Path(__file__).with_name("roles-daher.csv")
RECEPTION = "Daher Reception"
CLINICIAN = "Daher Clinician Admission"
ADD_ORDERS = "Add Orders"
GET_CONCEPTS = "Get Concepts"
GET_CARE_SETTINGS = "Get Care Settings"
GET_PROVIDERS = "Get Providers"
GET_ORDER_TYPES = "Get Order Types"

RECEPTION_PRIVILEGES = {
    "Edit Patient Identifiers",
    "Add Patients",
    "Get Patients",
}
CLINICIAN_PRIVILEGES = {
    "Get Patients",
    "Get Visit Types",
    "Add Visits",
    "Get Visits",
    "Get Visit Attribute Types",
    "Get Beds",
    "Get Admission Locations",
    "Add Encounters",
    "Get Encounters",
    ADD_ORDERS,
}


def read_roles(path: Path = CSV_PATH) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return {row["Role name"]: row for row in rows}


def privileges(row: dict[str, str]) -> set[str]:
    return {item.strip() for item in row["Privileges"].split(";") if item.strip()}


class ApprovedOrderPrivilegeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.roles = read_roles()

    def test_clinician_has_exact_approved_set_for_order_creation(self) -> None:
        self.assertEqual(privileges(self.roles[CLINICIAN]), CLINICIAN_PRIVILEGES)

    def test_reception_remains_exactly_registration_only(self) -> None:
        self.assertEqual(privileges(self.roles[RECEPTION]), RECEPTION_PRIVILEGES)
        self.assertNotIn(ADD_ORDERS, privileges(self.roles[RECEPTION]))
        self.assertNotIn(GET_CONCEPTS, privileges(self.roles[RECEPTION]))
        self.assertNotIn(GET_CARE_SETTINGS, privileges(self.roles[RECEPTION]))
        self.assertNotIn(GET_PROVIDERS, privileges(self.roles[RECEPTION]))
        self.assertNotIn(GET_ORDER_TYPES, privileges(self.roles[RECEPTION]))

    def test_no_other_daher_role_has_add_orders(self) -> None:
        holders = [name for name, row in self.roles.items() if ADD_ORDERS in privileges(row)]
        self.assertEqual(holders, [CLINICIAN])

    def test_no_daher_role_has_direct_get_concepts(self) -> None:
        holders = [name for name, row in self.roles.items() if GET_CONCEPTS in privileges(row)]
        self.assertEqual(holders, [])

    def test_no_daher_role_has_direct_get_care_settings(self) -> None:
        holders = [name for name, row in self.roles.items() if GET_CARE_SETTINGS in privileges(row)]
        self.assertEqual(holders, [])

    def test_no_daher_role_has_direct_get_providers(self) -> None:
        holders = [name for name, row in self.roles.items() if GET_PROVIDERS in privileges(row)]
        self.assertEqual(holders, [])

    def test_no_daher_role_has_direct_get_order_types(self) -> None:
        holders = [name for name, row in self.roles.items() if GET_ORDER_TYPES in privileges(row)]
        self.assertEqual(holders, [])

    def test_no_daher_role_has_broad_order_or_configuration_read(self) -> None:
        for row in self.roles.values():
            self.assertNotIn("Get Global Properties", privileges(row))
            self.assertNotIn("Get Orders", privileges(row))
            self.assertNotIn("Daher View System Configuration", privileges(row))

    def test_lab_tech_has_no_openmrs_role_grant_in_this_initializer(self) -> None:
        self.assertFalse(any("lab" in name.casefold() for name in self.roles))

    def test_roles_are_self_contained(self) -> None:
        self.assertTrue(all(not row["Inherited roles"].strip() for row in self.roles.values()))


if __name__ == "__main__":
    unittest.main()
