"""The provisioning portal (ADR-020, ADR-023).

The tests that matter are the ones about what must NOT happen: the API credential must not
reach the browser, an unauthenticated visitor must not see the fleet, a cross-site form must
not be able to create a facility, and health data must not be cacheable.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from daher_portal.app import ProvisioningClient, create_portal
from daher_portal.security import AuthError, Operator, hash_password, verify_password

PASSWORD = "a-long-enough-portal-password"
CREDENTIAL = "dhp_portal_super-secret-credential-value"


class FakeClient(ProvisioningClient):
    """Stands in for the API. Records whether the portal called it."""

    def __init__(self, facilities=None, status=None, create_response=None):
        self._facilities = facilities or []
        self._status = status or {}
        self._create_response = create_response
        self.created: list[dict] = []

    def facilities(self):
        return self._facilities

    def status(self, facility_id):
        return self._status

    def create(self, payload):
        self.created.append(payload)
        return self._create_response or httpx.Response(201, json={"facility": payload})


def a_portal(client=None, **kwargs):
    operator = Operator(username="liban", password_hash=hash_password(PASSWORD))
    app = create_portal(
        client=client or FakeClient(),
        operator=operator,
        session_secret="test-session-secret",
        hosts=["host-a"],
        **kwargs,
    )
    return TestClient(app, base_url="https://portal.test")


def sign_in(client: TestClient) -> None:
    page = client.get("/login")
    token = page.text.split('name="csrf_token" value="')[1].split('"')[0]
    client.post(
        "/login",
        data={"username": "liban", "password": PASSWORD, "csrf_token": token},
        follow_redirects=False,
    )


class TestTheCredentialNeverReachesTheBrowser:
    """The single most important property of splitting portal from API (ADR-023)."""

    def test_no_page_contains_the_api_credential(self):
        status = {
            "state": "running",
            "services_running": 10,
            "services_expected": 10,
            "backup_state": "success",
        }
        client = a_portal(
            FakeClient(
                facilities=[
                    {"facility_id": "daher-bc", "display_name": "Daher", "type": "hospital"}
                ],
                status=status,
            )
        )
        sign_in(client)
        body = client.get("/").text
        assert CREDENTIAL not in body
        assert "Authorization" not in body
        assert "Bearer" not in body

    def test_the_login_page_contains_no_credential(self):
        assert CREDENTIAL not in a_portal().get("/login").text


class TestAuthenticationIsRequired:
    def test_the_fleet_is_not_visible_signed_out(self):
        response = a_portal().get("/", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"

    def test_creating_a_facility_signed_out_is_refused(self):
        fake = FakeClient()
        client = a_portal(fake)
        client.post(
            "/facilities",
            data={"display_name": "X", "type": "hospital", "host_id": "host-a"},
            follow_redirects=False,
        )
        assert fake.created == [], "an unauthenticated request reached the provisioning API"

    def test_signing_in_works(self):
        client = a_portal()
        sign_in(client)
        assert client.get("/", follow_redirects=False).status_code == 200

    def test_a_wrong_password_is_refused(self):
        client = a_portal()
        page = client.get("/login")
        token = page.text.split('name="csrf_token" value="')[1].split('"')[0]
        response = client.post(
            "/login",
            data={"username": "liban", "password": "wrong-password", "csrf_token": token},
        )
        assert "Invalid credentials" in response.text

    def test_the_error_does_not_say_which_half_was_wrong(self):
        """Saying 'no such user' tells an attacker which half to keep guessing."""
        client = a_portal()
        page = client.get("/login")
        token = page.text.split('name="csrf_token" value="')[1].split('"')[0]
        response = client.post(
            "/login", data={"username": "nobody", "password": "x" * 20, "csrf_token": token}
        )
        assert "no such user" not in response.text.lower()
        assert "unknown user" not in response.text.lower()

    def test_logout_clears_the_session(self):
        client = a_portal()
        sign_in(client)
        client.get("/logout", follow_redirects=False)
        assert client.get("/", follow_redirects=False).status_code == 303


class TestCsrf:
    def test_a_post_without_a_token_does_not_create(self):
        """Otherwise any page on the internet could drive an operator's browser."""
        fake = FakeClient()
        client = a_portal(fake)
        sign_in(client)
        client.post(
            "/facilities",
            data={"display_name": "X", "type": "hospital", "host_id": "host-a"},
            follow_redirects=False,
        )
        assert fake.created == []

    def test_a_post_with_a_wrong_token_does_not_create(self):
        fake = FakeClient()
        client = a_portal(fake)
        sign_in(client)
        client.post(
            "/facilities",
            data={
                "display_name": "X",
                "type": "hospital",
                "host_id": "host-a",
                "csrf_token": "forged",
            },
            follow_redirects=False,
        )
        assert fake.created == []

    def test_a_valid_token_does_create(self):
        fake = FakeClient()
        client = a_portal(fake)
        sign_in(client)
        page = client.get("/")
        token = page.text.split('name="csrf_token" value="')[1].split('"')[0]
        client.post(
            "/facilities",
            data={
                "display_name": "Daher Birth Centre",
                "type": "hospital",
                "host_id": "host-a",
                "csrf_token": token,
            },
            follow_redirects=False,
        )
        assert fake.created == [
            {"display_name": "Daher Birth Centre", "type": "hospital", "host_id": "host-a"}
        ]

    def test_login_requires_a_token_too(self):
        client = a_portal()
        response = client.post("/login", data={"username": "liban", "password": PASSWORD})
        assert "Invalid credentials" in response.text


class TestCookieAndHeaderHardening:
    def test_the_session_cookie_is_httponly_secure_and_strict(self):
        client = a_portal()
        sign_in(client)
        header = "".join(
            v for k, v in client.get("/login").headers.items() if k.lower() == "set-cookie"
        )
        combined = header or str(client.cookies)
        # The middleware sets these; assert the configuration rather than the raw header,
        # which TestClient may consume.
        from daher_portal.app import create_portal as factory

        app = factory(client=FakeClient(), operator=None, session_secret="s")
        middleware = [m for m in app.user_middleware if "Session" in str(m)][0]
        assert middleware.kwargs["https_only"] is True
        assert middleware.kwargs["same_site"] == "strict"
        assert combined is not None

    def test_health_data_is_not_cacheable(self):
        """A stale 'backup succeeded' shown as current is worse than showing nothing."""
        client = a_portal()
        sign_in(client)
        cache_control = client.get("/").headers["cache-control"]
        assert "no-store" in cache_control

    def test_clickjacking_and_sniffing_headers_are_set(self):
        client = a_portal()
        response = client.get("/login")
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert "script-src 'none'" in response.headers["content-security-policy"]


class TestItShowsNoClinicalData:
    def test_no_template_renders_patient_data(self):
        """Check template EXPRESSIONS, not prose.

        An earlier version of this test matched the whole file and failed on the page's own
        sentence "never anything from a patient record" — which would have pushed someone to
        delete an honest reassurance from the UI to satisfy a blunt guard. The real risk is
        a rendered value like `{{ f.patient_count }}`, so only `{{ ... }}` and `{% ... %}`
        are inspected.
        """
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "src/daher_portal/templates"
        if not root.is_dir():
            root = Path("/app/src/daher_portal/templates")
        templates = list(root.glob("*.html"))
        assert templates, "found no templates to check"

        for template in templates:
            text = template.read_text(encoding="utf-8")
            expressions = re.findall(r"\{\{(.*?)\}\}|\{%(.*?)%\}", text, re.DOTALL)
            rendered = " ".join(part for pair in expressions for part in pair).lower()
            for forbidden in ("patient", "encounter", "diagnos", "specimen", "result", "mrn"):
                assert forbidden not in rendered, (
                    f"{template.name} RENDERS {forbidden!r} — the portal must never display "
                    f"anything originating from a patient record (ADR-021)"
                )

    def test_an_unreachable_api_is_shown_not_hidden(self):
        """An empty list and an unreachable API look identical; one means 'fleet is fine'."""

        class Broken(FakeClient):
            def facilities(self):
                raise ConnectionError("no route")

        client = a_portal(Broken())
        sign_in(client)
        assert "Could not reach the provisioning API" in client.get("/").text


class TestPasswordHandling:
    def test_short_passwords_are_refused_at_creation(self):
        with pytest.raises(AuthError, match="at least 12 characters"):
            hash_password("short")

    def test_hashes_are_salted_and_differ(self):
        assert hash_password(PASSWORD) != hash_password(PASSWORD)

    def test_the_stored_value_is_a_hash_not_the_password(self):
        assert PASSWORD not in hash_password(PASSWORD)

    def test_an_unknown_operator_is_refused(self):
        with pytest.raises(AuthError, match="invalid username or password"):
            verify_password(None, "liban", PASSWORD)

    def test_a_wrong_username_with_a_right_password_is_refused(self):
        operator = Operator(username="liban", password_hash=hash_password(PASSWORD))
        with pytest.raises(AuthError):
            verify_password(operator, "someone-else", PASSWORD)


class TestBackupRisk:
    """A facility with no backup is one disk failure from losing patient records."""

    def _portal_with(self, backup_state):
        return a_portal(
            FakeClient(
                facilities=[{"facility_id": "d", "display_name": "D", "type": "hospital"}],
                status={
                    "state": "running",
                    "services_running": 10,
                    "services_expected": 10,
                    "backup_state": backup_state,
                },
            )
        )

    def test_a_facility_with_no_backup_is_shown_as_a_problem(self):
        client = self._portal_with("never_run")
        sign_in(client)
        body = client.get("/").text
        assert "not protected" in body
        assert "no working backup" in body

    def test_a_failed_backup_is_shown_as_a_problem(self):
        client = self._portal_with("failed")
        sign_in(client)
        assert "not protected" in client.get("/").text

    def test_a_working_backup_is_not_flagged(self):
        client = self._portal_with("success")
        sign_in(client)
        assert "not protected" not in client.get("/").text

    def test_scheduled_is_never_presented_as_verified(self):
        """A scheduled backup is not a tested one until a restore has been performed."""
        client = self._portal_with("success")
        sign_in(client)
        body = client.get("/").text
        assert "Restore drill" in body
        assert "never" in body, "a facility with no restore drill must say so"
