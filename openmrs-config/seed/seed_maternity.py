#!/usr/bin/env python3
"""Add the configured Daher maternity bed types and beds through OpenMRS REST.

This seed intentionally has no update, retire, void, or delete operation. Existing
objects must match the committed definition; conflicts stop deployment for review.
Lower configured counts therefore leave every existing bed untouched (ADR-007).
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

WARD_UUID = "6538f097-382d-49b6-a839-601d03c888c0"
WARD_NAME = "Maternité"
ADMISSION_TAG = "Admission Location"
PAGE_SIZE = 100
MAX_BEDS_PER_TYPE = 9999


class SeedError(RuntimeError):
    """A configuration or remote-state conflict that must stop the seed."""


@dataclass(frozen=True)
class BedKind:
    count_variable: str
    type_name: str
    display_name: str
    description: str
    number_prefix: str
    row: int


BED_KINDS = (
    BedKind(
        "MATERNITY_DELIVERY_BEDS",
        "Salle d'accouchement",
        "Accouch.",
        "Salle d'accouchement",
        "DEL",
        1,
    ),
    BedKind(
        "MATERNITY_POSTPARTUM_BEDS",
        "Post-partum",
        "Post-part.",
        "Post-partum",
        "POST",
        2,
    ),
)


class OpenMRSAPI:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}", "Accept": "application/json"}

    def request(
        self,
        method: str,
        resource: str,
        *,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = urllib.parse.urljoin(self.base_url, resource.lstrip("/"))
        if urllib.parse.urlsplit(url).scheme not in {"http", "https"}:
            raise SeedError(f"OpenMRS URL must use http or https: {url!r}")
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = None
        headers = dict(self.headers)
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(  # noqa: S310 -- scheme is constrained above
            url, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(  # noqa: S310 -- request URL is constrained above
                request, timeout=30
            ) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read(2048).decode(errors="replace")
            raise SeedError(f"OpenMRS {method} {resource} failed ({exc.code}): {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise SeedError(f"OpenMRS {method} {resource} failed: {exc}") from exc

    def get(self, resource: str, *, query: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("GET", resource, query=query)

    def create(self, resource: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", resource, body=body)

    def all_results(
        self, resource: str, *, query: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        start = 0
        while True:
            page_query = dict(query or {})
            page_query.update({"limit": PAGE_SIZE, "startIndex": start, "v": "full"})
            page = self.get(resource, query=page_query)
            batch = page.get("results")
            if not isinstance(batch, list):
                raise SeedError(f"OpenMRS {resource} returned no results list")
            results.extend(batch)
            if len(batch) < PAGE_SIZE:
                return results
            start += PAGE_SIZE


def configured_count(name: str) -> int:
    raw = os.environ.get(name, "0")
    try:
        count = int(raw)
    except ValueError as exc:
        raise SeedError(f"{name} must be an integer, got {raw!r}") from exc
    if not 0 <= count <= MAX_BEDS_PER_TYPE:
        raise SeedError(f"{name} must be between 0 and {MAX_BEDS_PER_TYPE}, got {count}")
    return count


def require_single(items: list[dict[str, Any]], description: str) -> dict[str, Any]:
    if len(items) != 1:
        raise SeedError(f"Expected exactly one {description}; found {len(items)}")
    return items[0]


def ensure_ward(api: OpenMRSAPI) -> None:
    ward = api.get(f"location/{WARD_UUID}", query={"v": "full"})
    if ward.get("name") != WARD_NAME or ward.get("retired") is not False:
        raise SeedError(f"Location {WARD_UUID} does not match the active {WARD_NAME!r} ward")
    tag_names = {tag.get("name") for tag in ward.get("tags", [])}
    if ADMISSION_TAG not in tag_names:
        raise SeedError(f"Ward {WARD_NAME!r} is missing the {ADMISSION_TAG!r} tag")


def ensure_bed_type(api: OpenMRSAPI, kind: BedKind) -> None:
    exact = [
        item
        for item in api.all_results("bedtype", query={"name": kind.type_name})
        if item.get("name") == kind.type_name
    ]
    if not exact:
        api.create(
            "bedtype",
            {
                "name": kind.type_name,
                "displayName": kind.display_name,
                "description": kind.description,
            },
        )
        return

    existing = require_single(exact, f"bed type named {kind.type_name!r}")
    expected = {"displayName": kind.display_name, "description": kind.description}
    mismatches = {
        key: (existing.get(key), value)
        for key, value in expected.items()
        if existing.get(key) != value
    }
    if mismatches:
        raise SeedError(
            f"Bed type {kind.type_name!r} conflicts with committed configuration: {mismatches}"
        )


def bed_number(kind: BedKind, index: int) -> str:
    return f"{kind.number_prefix}-{index:04d}"


def reconcile_beds(api: OpenMRSAPI, kind: BedKind, desired_count: int) -> tuple[int, int]:
    beds = api.all_results("bed", query={"locationUuid": WARD_UUID})
    by_number: dict[str, dict[str, Any]] = {}
    by_position: dict[tuple[int, int], dict[str, Any]] = {}
    for bed in beds:
        number = bed.get("bedNumber")
        position = (bed.get("row"), bed.get("column"))
        if number in by_number:
            raise SeedError(f"Ward has duplicate bed number {number!r}")
        if position in by_position:
            raise SeedError(f"Ward has duplicate bed position {position!r}")
        by_number[number] = bed
        by_position[position] = bed

    created = 0
    for index in range(1, desired_count + 1):
        number = bed_number(kind, index)
        position = (kind.row, index)
        existing = by_number.get(number)
        occupant = by_position.get(position)
        if existing is not None:
            type_name = (existing.get("bedType") or {}).get("name")
            if type_name != kind.type_name or position != (
                existing.get("row"),
                existing.get("column"),
            ):
                raise SeedError(f"Bed {number!r} conflicts with committed type or position")
            if occupant is not existing:
                raise SeedError(f"Bed position {position!r} is occupied by another bed")
            continue
        if occupant is not None:
            raise SeedError(
                f"Bed position {position!r} is occupied by {occupant.get('bedNumber')!r}"
            )

        created_bed = api.create(
            "bed",
            {
                "bedNumber": number,
                "bedType": kind.type_name,
                "row": kind.row,
                "column": index,
                "locationUuid": WARD_UUID,
            },
        )
        if created_bed.get("bedNumber") != number:
            raise SeedError(f"OpenMRS did not confirm creation of bed {number!r}")
        created += 1
        by_number[number] = created_bed
        by_position[position] = created_bed

    retained_above_count = sum(
        1
        for index in range(desired_count + 1, MAX_BEDS_PER_TYPE + 1)
        if bed_number(kind, index) in by_number
    )
    return created, retained_above_count


def run(api: OpenMRSAPI) -> None:
    ensure_ward(api)
    counts = {kind: configured_count(kind.count_variable) for kind in BED_KINDS}
    for kind in BED_KINDS:
        ensure_bed_type(api, kind)
    for kind in BED_KINDS:
        created, retained = reconcile_beds(api, kind, counts[kind])
        print(
            f"{kind.type_name}: desired={counts[kind]} created={created} "
            f"retained_above_count={retained}"
        )


def main() -> int:
    required = ("OPENMRS_API_URL", "OPENMRS_ADMIN_PASSWORD")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise SeedError(f"Missing required environment variables: {', '.join(missing)}")
    api = OpenMRSAPI(os.environ["OPENMRS_API_URL"], "admin", os.environ["OPENMRS_ADMIN_PASSWORD"])
    run(api)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SeedError as exc:
        print(f"Maternity seed refused to continue: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
