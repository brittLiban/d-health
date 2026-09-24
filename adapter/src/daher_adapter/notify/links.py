"""DH-NOTIFY-001 §17 — composing a patient access URL server-side.

The browser already has its own builder (``ui/src/features/patient/patientAccessUrl.ts``) for the
QR and the printed sheet, and it takes ``window.location.origin`` because a locally-rendered QR must
resolve to the origin the clinician is actually using. A **server-composed** link cannot do that:
the only origin a request offers is the ``Host`` header, which the caller controls, and a link built
from an attacker-supplied host would carry a live patient bearer credential to an attacker-chosen
destination. So the origin here is explicit deployment configuration and nothing else.

The two builders must agree on the *shape*, and they do, for the reason
``patientAccessUrl.ts`` states: the credential travels in the **fragment**. A fragment is not sent
to the HTTP server as part of a request, so it cannot reach reverse-proxy access logs, server
routing or a referrer header. ``?access=`` would put a live credential into every one of those.
``tests/test_patient_notification_content.py`` pins the fragment form, and ``NC-NOTIFY-4`` moves it
into a query parameter to prove the test is the thing enforcing it.
"""

from __future__ import annotations

#: Must match `ACCESS_FRAGMENT_KEY` in `ui/src/features/patient/patientAccessUrl.ts`. Two builders,
#: one contract; a divergence here produces links that open the patient app with no credential.
ACCESS_FRAGMENT_KEY = "access"


def build_patient_access_url(base_url: str, raw_token: str) -> str:
    """``<base>/?app=patient#access=<token>``.

    ``?app=patient`` is preserved deliberately, matching the browser builder: a refresh must land
    back on the patient app rather than on the clinician-facing surface.

    Raises rather than composing a half-formed link. A URL with no origin, or a message with no
    credential in it, is worse than no message: the patient follows it, sees a generic "scan again"
    screen, and concludes DHealth is broken.
    """
    origin = (base_url or "").strip().rstrip("/")
    if not origin:
        raise ValueError("a patient public base URL is required to compose an access link")
    if not raw_token:
        raise ValueError("a raw access token is required to compose an access link")
    return f"{origin}/?app=patient#{ACCESS_FRAGMENT_KEY}={raw_token}"
