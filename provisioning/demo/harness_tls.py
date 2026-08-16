"""TLS controls for the demonstration (ADR-022).

A TLS test that only ever presents the correct certificate proves nothing — `verify=False`
would pass it identically. So each control below is a case that MUST fail, and the point is
to observe it failing for the right reason.

Run inside the agent container, where the CA material and the real network path both exist.
"""

from __future__ import annotations

import os

import httpx

URL = os.environ.get("PROVISIONING_URL", "https://api:8443")
REAL_CA = "/shared/tls/real-ca.pem"
BOGUS_CA = "/shared/tls/bogus-ca.pem"
CREDENTIAL = open("/shared/agent_credential", encoding="utf-8").read().strip()  # noqa: SIM115
HEADERS = {"Authorization": f"Bearer {CREDENTIAL}"}


def attempt(label: str, *, url: str, verify, expect_success: bool) -> bool:
    try:
        response = httpx.get(f"{url}/agent/jobs/claim", headers=HEADERS, verify=verify, timeout=8)
        outcome, detail = True, f"HTTP {response.status_code}"
    except Exception as exc:  # noqa: BLE001 - the failure reason is the result here
        outcome, detail = False, f"{type(exc).__name__}: {str(exc)[:90]}"

    ok = outcome is expect_success
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    print(f"         expected {'success' if expect_success else 'refusal'} -> {detail}")
    return ok


if __name__ == "__main__":
    import socket

    # Resolve the API to an IP so the hostname-mismatch control reaches the real listener.
    api_ip = socket.gethostbyname("api")
    print("=== TLS controls: only the first should succeed ===")
    print(f"  (api resolves to {api_ip}; its certificate carries SAN:api only)")
    results = [
        attempt("real CA, correct hostname", url=URL, verify=REAL_CA, expect_success=True),
        # If this succeeds, the agent is not verifying at all.
        attempt("BOGUS CA (unrelated issuer)", url=URL, verify=BOGUS_CA, expect_success=False),
        # If this succeeds, hostname verification is off even though the CA is trusted.
        #
        # ⚠️ It must reach the REAL server by an address the certificate does not cover. An
        # earlier version used 127.0.0.1, which has nothing listening inside this container
        # — it "passed" with ConnectionRefused, never reaching TLS at all. That is a control
        # passing for the wrong reason, which is worse than no control.
        attempt(
            f"real CA, WRONG hostname (IP {api_ip}, cert is SAN:api)",
            url=f"https://{api_ip}:8443",
            verify=REAL_CA,
            expect_success=False,
        ),
        # The system trust store must not contain this private CA.
        attempt("system trust store only", url=URL, verify=True, expect_success=False),
    ]
    print(f"\n  {sum(results)}/{len(results)} controls behaved correctly")
    raise SystemExit(0 if all(results) else 1)
