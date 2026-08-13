import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).with_name("seed_maternity.py")
SPEC = importlib.util.spec_from_file_location("seed_maternity", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Unable to load seed_maternity.py")
seed = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = seed
SPEC.loader.exec_module(seed)


class FakeAPI:
    def __init__(self, beds=None, types=None):
        self.beds = list(beds or [])
        self.types = list(types or [])
        self.created = []

    def all_results(self, resource, query=None):
        if resource == "bed":
            return list(self.beds)
        if resource == "bedtype":
            name = query["name"]
            return [item for item in self.types if item["name"] == name]
        raise AssertionError(resource)

    def create(self, resource, body):
        self.created.append((resource, body))
        if resource == "bed":
            return {**body, "bedType": {"name": body["bedType"]}}
        return body


class SeedTests(unittest.TestCase):
    def test_invalid_count_fails_closed(self):
        with patch.dict(os.environ, {"MATERNITY_DELIVERY_BEDS": "-1"}):
            with self.assertRaises(seed.SeedError):
                seed.configured_count("MATERNITY_DELIVERY_BEDS")

    def test_seed_adds_only_missing_beds(self):
        kind = seed.BED_KINDS[0]
        api = FakeAPI(
            beds=[
                {
                    "bedNumber": "DEL-0001",
                    "bedType": {"name": kind.type_name},
                    "row": 1,
                    "column": 1,
                }
            ]
        )
        created, retained = seed.reconcile_beds(api, kind, 2)
        self.assertEqual((created, retained), (1, 0))
        self.assertEqual(api.created[0][1]["bedNumber"], "DEL-0002")

    def test_lower_count_never_deletes_or_updates(self):
        kind = seed.BED_KINDS[1]
        beds = [
            {
                "bedNumber": f"POST-{index:04d}",
                "bedType": {"name": kind.type_name},
                "row": 2,
                "column": index,
            }
            for index in range(1, 4)
        ]
        api = FakeAPI(beds=beds)
        created, retained = seed.reconcile_beds(api, kind, 1)
        self.assertEqual((created, retained), (0, 2))
        self.assertEqual(api.created, [])

    def test_conflicting_position_fails_without_writes(self):
        kind = seed.BED_KINDS[0]
        api = FakeAPI(
            beds=[
                {
                    "bedNumber": "OTHER",
                    "bedType": {"name": "Other"},
                    "row": 1,
                    "column": 1,
                }
            ]
        )
        with self.assertRaises(seed.SeedError):
            seed.reconcile_beds(api, kind, 1)
        self.assertEqual(api.created, [])

    def test_conflicting_type_is_not_modified(self):
        kind = seed.BED_KINDS[0]
        api = FakeAPI(
            types=[
                {"name": kind.type_name, "displayName": "wrong", "description": kind.description}
            ]
        )
        with self.assertRaises(seed.SeedError):
            seed.ensure_bed_type(api, kind)
        self.assertEqual(api.created, [])


if __name__ == "__main__":
    unittest.main()
