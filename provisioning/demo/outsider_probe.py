"""An address that is NOT in the API's allowlist. Everything it tries must be refused."""

from __future__ import annotations

import httpx

API = "https://api:8443"
CA = "/shared/tls/real-ca.pem"

if __name__ == "__main__":
    credential = open("/shared/portal_key").read().strip()  # a genuinely valid credential
    print("=== outsider holds a VALID portal credential but a disallowed address ===")
    for path in ("/facilities", "/agent/jobs/claim"):
        try:
            r = httpx.get(f"{API}{path}", headers={"Authorization": f"Bearer {credential}"},
                          verify=CA, timeout=8)
            print(f"  {path:22s} -> {r.status_code} {r.json().get('detail','')}")
        except Exception as exc:
            print(f"  {path:22s} -> {type(exc).__name__}")
    print("=== can the outsider reach the agent at all? ===")
    import socket
    for port in (8000, 8443, 9000):
        s = socket.socket(); s.settimeout(1.5)
        try:
            s.connect(("agent", port)); print(f"  agent:{port} OPEN")
        except Exception as e:
            print(f"  agent:{port} refused ({type(e).__name__})")
        finally:
            s.close()
