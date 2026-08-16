"""Demonstration portal: the REAL portal app, pointed at the real API over TLS."""

from __future__ import annotations

import os
import time
from pathlib import Path

import uvicorn

from daher_portal.app import ProvisioningClient, create_portal
from daher_portal.security import hash_password

SHARED = Path(os.environ.get("DEMO_SHARED", "/shared"))
PASSWORD = "demo-portal-password-1234"

if __name__ == "__main__":
    while not ((SHARED / "portal_key").is_file() and (SHARED / "tls" / "portal.pem").is_file()):
        time.sleep(0.5)

    credential = (SHARED / "portal_key").read_text(encoding="utf-8").strip()
    (SHARED / "portal_password").write_text(PASSWORD, encoding="utf-8")

    client = ProvisioningClient(
        os.environ["PROVISIONING_URL"], credential, verify=str(SHARED / "tls" / "real-ca.pem")
    )
    app = create_portal(
        client=client,
        operator=__import__("daher_portal.security", fromlist=["Operator"]).Operator(
            username="liban", password_hash=hash_password(PASSWORD)
        ),
        session_secret="demo-session-secret-not-for-real-use",
        hosts=["host-a"],
    )
    (SHARED / "portal_ready").write_text("ok", encoding="utf-8")
    tls = SHARED / "tls"
    uvicorn.run(
        app,
        host="0.0.0.0",  # noqa: S104
        port=9000,
        log_level="warning",
        ssl_certfile=str(tls / "portal.pem"),
        ssl_keyfile=str(tls / "portal.key"),
    )
