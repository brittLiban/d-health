#!/usr/bin/env python3
"""Create the Daher prenatal lab order set in OpenMRS, from an explicitly signed-off member list.

DH-006. This seed deliberately ships with ZERO members. An empty panel is the correct
committed state: neither OpenMRS nor OpenELIS ships a prenatal panel to copy unmodified,
so its contents must be composed, and composing them is a clinical decision reserved to
the clinical lead (AGENTS.md; NEEDS LIBAN #9).

Like the DH-003 bed seed, this has no update, retire, void, or delete operation. It adds
members that are listed and absent; it never removes one that is present but unlisted.
A member may already have been used to order tests for a patient, and retiring it is a
clinical/audit action rather than a config reconciliation (same reasoning as ADR-007).

Initializer 2.12.0 has no order-set domain — checked against the deployed module's own
config directory — so this uses the native REST seam, exactly as DH-003 did for beds.
"""

from __future__ import annotations

import base64
import csv
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

ORDER_SET_UUID = "b8f4d5cd-6f3a-4c1b-9d3e-5a0f4c9b7e21"
ORDER_SET_NAME = "Bilan prénatal"
ORDER_SET_DESCRIPTION = "Bilan prénatal de routine — contenu en attente de validation clinique"
# OpenMRS ships this operator meaning "all members apply", which is what a routine
# panel is. ANY/ONE would make the set a choice between tests, which is a different
# clinical object and not what a panel is.
OPERATOR = "ALL"
PAGE_SIZE = 100
MAX_MEMBERS = 200


class SeedError(RuntimeError):
    """A configuration or remote-state conflict that must stop the seed."""


@dataclass(frozen=True)
class PanelMember:
    concept_uuid: str
    label: str


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


def read_members(path: str) -> list[PanelMember]:
    """Read the signed-off member list. A missing or empty file is VALID, not an error."""
    if not os.path.exists(path):
        return []

    members: list[PanelMember] = []
    seen: set[str] = set()
    with open(path, encoding="utf-8") as handle:
        # Comments carry the "do not populate this yourself" warning, so they must
        # survive in the file rather than being stripped from the committed copy.
        rows = [line for line in handle if not line.lstrip().startswith("#")]

    for row in csv.DictReader(rows):
        raw_uuid = (row.get("concept_uuid") or "").strip()
        if not raw_uuid:
            continue
        if raw_uuid in seen:
            raise SeedError(
                f"concept {raw_uuid!r} is listed twice in {path}; a panel member must appear once"
            )
        seen.add(raw_uuid)
        members.append(PanelMember(raw_uuid, (row.get("label") or "").strip()))

    if len(members) > MAX_MEMBERS:
        raise SeedError(f"{len(members)} members exceeds the {MAX_MEMBERS} sanity limit")
    return members


def verify_concepts_exist(api: OpenMRSAPI, members: list[PanelMember]) -> None:
    """Every listed concept must already exist. This seed never creates clinical concepts."""
    for member in members:
        try:
            concept = api.get(
                f"concept/{member.concept_uuid}", query={"v": "custom:(uuid,display)"}
            )
        except SeedError as exc:
            raise SeedError(
                f"concept {member.concept_uuid!r} ({member.label or 'no label'}) "
                f"was not found in OpenMRS. This seed does not invent clinical concepts — "
                f"confirm the UUID against docs/CLINICAL-SIGNOFF.md. Original error: {exc}"
            ) from exc
        if not concept.get("uuid"):
            raise SeedError(f"concept {member.concept_uuid!r} returned no uuid")


def find_order_set(api: OpenMRSAPI) -> dict[str, Any] | None:
    try:
        found = api.get(f"orderset/{ORDER_SET_UUID}", query={"v": "full"})
    except SeedError:
        return None
    return found if found.get("uuid") else None


def existing_member_concepts(order_set: dict[str, Any]) -> set[str]:
    concepts: set[str] = set()
    for member in order_set.get("orderSetMembers") or []:
        if member.get("retired"):
            continue
        concept = member.get("concept") or {}
        uuid = concept.get("uuid")
        if uuid:
            concepts.add(uuid)
    return concepts


def seed(api: OpenMRSAPI, members: list[PanelMember]) -> int:
    verify_concepts_exist(api, members)

    order_set = find_order_set(api)
    if order_set is None:
        api.create(
            "orderset",
            {
                "uuid": ORDER_SET_UUID,
                "name": ORDER_SET_NAME,
                "description": ORDER_SET_DESCRIPTION,
                "operator": OPERATOR,
                "orderSetMembers": [],
            },
        )
        print(f"created order set {ORDER_SET_NAME!r} with 0 members")
        order_set = find_order_set(api)
        if order_set is None:
            raise SeedError("order set was created but could not be read back")
    else:
        print(f"order set {ORDER_SET_NAME!r} already exists")

    present = existing_member_concepts(order_set)
    unlisted = present - {m.concept_uuid for m in members}
    if unlisted:
        # Not an error: removal is deliberately not automated. Say so loudly rather
        # than silently diverging from the committed file.
        print(
            f"NOTE: {len(unlisted)} member(s) exist in OpenMRS but are not in the "
            f"committed list. They are LEFT IN PLACE — removing a panel member is a "
            f"clinical/audit action, not a config reconciliation."
        )

    added = 0
    for member in members:
        if member.concept_uuid in present:
            continue
        api.create(
            f"orderset/{ORDER_SET_UUID}/ordersetmember",
            {
                "orderType": None,
                "orderTemplate": None,
                "concept": member.concept_uuid,
                "retired": False,
            },
        )
        added += 1
        print(f"  added member {member.label or member.concept_uuid}")

    if not members:
        print(
            "0 members configured — this is the expected committed state until the "
            "clinical lead signs off (docs/CLINICAL-SIGNOFF.md). DH-006 stays OPEN."
        )
    return added


def main() -> int:
    base_url = os.environ.get("OPENMRS_BASE_URL", "http://openmrs-backend:8080/openmrs/ws/rest/v1/")
    username = os.environ.get("OPENMRS_USERNAME", "admin")
    password = os.environ.get("OPENMRS_ADMIN_PASSWORD", "")
    members_path = os.environ.get("PRENATAL_PANEL_MEMBERS", "/seed/prenatal-panel.members.csv")
    if not password:
        print("OPENMRS_ADMIN_PASSWORD is not set", file=sys.stderr)
        return 2

    try:
        members = read_members(members_path)
        added = seed(OpenMRSAPI(base_url, username, password), members)
    except SeedError as exc:
        print(f"prenatal panel seed failed: {exc}", file=sys.stderr)
        return 1
    print(f"prenatal panel seed complete; {added} member(s) added")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
