"""Enqueue one legitimate job and several hostile VARIANTS, as a compromised API would.

The variants are deliberately different shapes from the ones in the unit tests. A refusal
that only works for the exact payload already tested would be validation narrower than it
looks, so these probe different mechanisms: a space, a traversal with slashes, an extra
top-level key naming volumes, a command substitution in a different field, wrong case, and
a job addressed to another host.
"""

from __future__ import annotations

import datetime
import json

import httpx

client = httpx.Client(base_url="http://localhost:8000", timeout=10)
NOW = datetime.datetime.now(datetime.UTC).isoformat()


def valid(job_id: str, **override) -> dict:
    job = {
        "job_id": job_id,
        "host_id": "host-a",
        "verb": "provision",
        "params": {
            "facility_id": "daher-bc",
            "facility_type": "hospital",
            "base_domain": "localhost",
        },
        "issued_at": NOW,
    }
    job.update(override)
    return job


def params(**over) -> dict:
    base = {"facility_id": "daher-bc", "facility_type": "hospital", "base_domain": "localhost"}
    base.update(over)
    return base


VARIANTS = {
    "job-hostile-space": valid("job-hostile-space", params=params(facility_id="daher-bc evil")),
    "job-hostile-travrs": valid(
        "job-hostile-travrs", params=params(facility_id="daher-bc/../../etc")
    ),
    "job-hostile-volumes": {**valid("job-hostile-volumes"), "volumes": ["/:/host"]},
    "job-hostile-domain": valid("job-hostile-domain", params=params(base_domain="evil$(id)")),
    "job-hostile-case": valid("job-hostile-case", params=params(facility_id="Daher-BC")),
    "job-hostile-otherhost": valid("job-hostile-otherhost", host_id="host-zzz"),
}

if __name__ == "__main__":
    print(json.dumps(client.post("/demo/enqueue-raw", json=valid("job-happy-0001")).json()))
    for payload in VARIANTS.values():
        client.post("/demo/enqueue-raw", json=payload)
    print(f"enqueued {len(VARIANTS)} hostile variants")
