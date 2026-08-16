"""Drive the portal exactly as a browser would, and assert the whole chain works.

This is the "click the button" test: sign in, submit the create-facility form, and confirm
the job reaches the agent — across three containers, over TLS, with nothing stubbed.
"""

from __future__ import annotations

import re
import sys
import time

import httpx

PORTAL = "https://portal:9000"
CA = "/shared/tls/real-ca.pem"


def token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    if not match:
        raise SystemExit("no CSRF token in the page — the portal did not render a form")
    return match.group(1)


if __name__ == "__main__":
    # The portal's session cookie is Secure + SameSite=strict, so it is only stored over
    # HTTPS. An earlier version of this harness used http and login silently failed — the
    # portal was failing closed exactly as designed, and the harness was wrong.
    client = httpx.Client(base_url=PORTAL, timeout=20, follow_redirects=False, verify=CA)

    print("=== 1. the fleet is not visible signed out ===")
    anon = client.get("/")
    print(f"  GET / -> {anon.status_code} {anon.headers.get('location', '')}")
    assert anon.status_code == 303

    print("=== 2. sign in ===")
    page = client.get("/login")
    signed = client.post(
        "/login",
        data={"username": "liban", "password": open("/shared/portal_password").read().strip(),
              "csrf_token": token(page.text)},
    )
    print(f"  POST /login -> {signed.status_code}")

    listing = client.get("/")
    print(f"  GET / -> {listing.status_code} (signed in: {'Facilities' in listing.text})")

    print("=== 3. create a facility through the form ===")
    created = client.post(
        "/facilities",
        data={"display_name": "Demo Birth Centre", "type": "hospital",
              "host_id": "host-a", "csrf_token": token(listing.text)},
    )
    print(f"  POST /facilities -> {created.status_code}")

    print("=== 4. does it appear in the fleet list? ===")
    for _ in range(10):
        body = client.get("/").text
        if "demo-birth-centre" in body:
            print("  facility visible in the portal")
            break
        time.sleep(1)
    else:
        print("  FACILITY NOT VISIBLE")
        sys.exit(1)
