from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import URL

_DB_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# DH-011/BLOCKER-6 (Option F): OpenELIS's SITEYEARNUM accession format needs a real numeric
# facility site code before any ServiceRequest can be created (DH-011B, live-reconfirmed this
# pass: `site_information` still carries the upstream placeholder). `DEV01` is the shipped
# upstream default -- refusing it is the one thing this repo can safely check in code, because
# it is proven from the running config, not guessed. The real value is NEEDS LIBAN #7 and is
# never invented here.
_KNOWN_PLACEHOLDER_SITE_CODES = frozenset({"DEV01"})


class ConfigurationError(ValueError):
    """Configuration is missing or unsafe."""


def _optional_site_code(environment: Mapping[str, str], name: str) -> str | None:
    """`None` until the real facility value exists -- never a placeholder, never invented.

    Validates only what DH-011B actually proved from the running OpenELIS config
    (`SITEYEARNUM`, numeric, `DEV01` refused): non-empty, digits only, and not the known
    upstream placeholder. It does NOT enforce a specific digit count -- the constant-pool read
    established the *overall* generated accession is 13 digits, not how many of those belong to
    the site code itself versus the year/sequence portions, and that split was never confirmed
    from source. Asserting an unverified width here would be exactly the kind of invented
    facility semantics this ticket forbids.
    """
    value = environment.get(name, "").strip()
    if not value:
        return None
    if value in _KNOWN_PLACEHOLDER_SITE_CODES:
        raise ConfigurationError(
            f"{name} is still the upstream placeholder {value!r} -- the real facility site "
            f"code has not been supplied (NEEDS LIBAN #7, PROJECT_STATE.md BLOCKER-6)"
        )
    if not value.isdigit():
        raise ConfigurationError(
            f"{name} must be numeric -- OpenELIS's SITEYEARNUM accession format refuses a "
            f"non-numeric site code (DH-011B)"
        )
    return value


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        raise ConfigurationError(f"{name} is required")
    return value


def _positive_int(environment: Mapping[str, str], name: str, default: int) -> int:
    raw = environment.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be an integer") from error
    if value <= 0:
        raise ConfigurationError(f"{name} must be greater than zero")
    return value


def _bounded_int(
    environment: Mapping[str, str], name: str, default: int, *, minimum: int, maximum: int
) -> int:
    raw = environment.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be an integer") from error
    if not (minimum <= value <= maximum):
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value


def _positive_float(environment: Mapping[str, str], name: str, default: float) -> float:
    raw = environment.get(name, str(default))
    try:
        value = float(raw)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be a number") from error
    if value <= 0:
        raise ConfigurationError(f"{name} must be greater than zero")
    return value


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"", "0", "false", "no", "off"})

#: Providers this build can actually construct. An unknown name is a startup refusal rather than a
#: silent fallback to the default: "I set DHEALTH_NOTIFICATION_PROVIDER=meta and nothing happened"
#: must not be a thing that can occur.
_KNOWN_NOTIFICATION_PROVIDERS = frozenset({"twilio", "fake"})


def _boolean(environment: Mapping[str, str], name: str, default: bool) -> bool:
    raw = environment.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    raise ConfigurationError(f"{name} must be a boolean (true/false)")


def _notification_settings(environment: Mapping[str, str]) -> dict[str, object]:
    """DH-NOTIFY-001 §10 — fail closed when sending is switched on but cannot work.

    The asymmetry is the point. With notifications **off**, every value below is optional and the
    adapter starts normally -- that is the state every test, every development stack and the native
    demo run in, and it must stay frictionless. With notifications **on**, a missing credential is a
    refusal to start, naming the variable, rather than a process that looks deployed and quietly
    sends nothing. §10 requires those two to be different states, and a half-configured service that
    boots green is precisely the shape that makes them look the same.

    The one that is easy to miss and is checked anyway: `DHEALTH_PATIENT_PUBLIC_BASE_URL`. Without
    it there is no way to compose a patient link at all, so enabling notifications without it would
    produce an access-link message that cannot be rendered -- discovered at the first patient
    handoff rather than at boot.
    """
    enabled = _boolean(environment, "DHEALTH_NOTIFICATIONS_ENABLED", False)
    provider = environment.get("DHEALTH_NOTIFICATION_PROVIDER", "").strip().lower() or "twilio"
    if provider not in _KNOWN_NOTIFICATION_PROVIDERS:
        raise ConfigurationError(
            f"DHEALTH_NOTIFICATION_PROVIDER must be one of "
            f"{', '.join(sorted(_KNOWN_NOTIFICATION_PROVIDERS))}"
        )

    base_url = environment.get("DHEALTH_PATIENT_PUBLIC_BASE_URL", "").strip() or None
    callback_url = environment.get("DHEALTH_NOTIFICATION_STATUS_CALLBACK_URL", "").strip() or None
    account_sid = environment.get("TWILIO_ACCOUNT_SID", "").strip() or None
    auth_token = environment.get("TWILIO_AUTH_TOKEN", "").strip() or None
    sms_from = environment.get("DHEALTH_SMS_FROM", "").strip() or None
    whatsapp_from = environment.get("DHEALTH_WHATSAPP_FROM", "").strip() or None
    region = environment.get("DHEALTH_NOTIFICATION_DEFAULT_REGION", "").strip().upper() or None

    if region is not None and (len(region) != 2 or not region.isalpha()):
        raise ConfigurationError(
            "DHEALTH_NOTIFICATION_DEFAULT_REGION must be an ISO 3166-1 alpha-2 code (e.g. DJ)"
        )

    if enabled:
        if base_url is None:
            raise ConfigurationError(
                "DHEALTH_PATIENT_PUBLIC_BASE_URL is required when "
                "DHEALTH_NOTIFICATIONS_ENABLED is true -- a patient link cannot be composed "
                "without it, and it must never be derived from a request Host header"
            )
        if not base_url.startswith("https://"):
            # A patient-access URL carries a live bearer credential in its fragment. Over http://
            # that fragment is exposed to anyone on the path before TLS would have protected it.
            raise ConfigurationError("DHEALTH_PATIENT_PUBLIC_BASE_URL must be an https:// URL")
        if provider == "twilio":
            if account_sid is None:
                raise ConfigurationError(
                    "TWILIO_ACCOUNT_SID is required when notifications are enabled"
                )
            if auth_token is None:
                raise ConfigurationError(
                    "TWILIO_AUTH_TOKEN is required when notifications are enabled"
                )
            if sms_from is None and whatsapp_from is None:
                raise ConfigurationError(
                    "at least one of DHEALTH_SMS_FROM or DHEALTH_WHATSAPP_FROM is required "
                    "when notifications are enabled"
                )

    return {
        "notifications_enabled": enabled,
        "notification_provider": provider,
        "patient_public_base_url": base_url,
        "notification_status_callback_url": callback_url,
        "twilio_account_sid": account_sid,
        "twilio_auth_token": auth_token,
        "sms_from": sms_from,
        "whatsapp_from": whatsapp_from,
        "notification_default_region": region,
    }


@dataclass(frozen=True, slots=True)
class Settings:
    timezone: str
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    audit_path: Path
    retry_max_attempts: int
    retry_multiplier_seconds: float
    retry_max_seconds: float
    openelis_site_code: str | None = None
    # DH-011E4: only the dead-letter retry CLI reads these -- the running FastAPI service does
    # not talk to OpenMRS at all yet (DH-011E is BUILT + VERIFIED, NOT ACTIVATED). Optional so
    # every existing deployment's `Settings.from_env()` keeps working unchanged.
    omrs_base_url: str | None = None
    omrs_auth: str | None = None
    # DH-EX-002: the Exchange's own OIDC identity boundary -- deliberately distinct from the
    # connector `daher` Keycloak realm (keycloak/realm-config/daher-realm.json), which stays
    # untouched. All three optional so every existing deployment keeps working unchanged; the
    # Exchange API fails closed (401 on every request) when any is unset, rather than refusing to
    # start -- see exchange/identity_auth.py's `UnconfiguredKeySource`.
    exchange_oidc_issuer: str | None = None
    exchange_oidc_audience: str | None = None
    exchange_oidc_jwks_url: str | None = None
    # DH-CONNECT-AGENT-001 §8/§10: a **second audience**, for connector agents only. Same issuer and
    # same signing keys; a different `aud`, validated by a separate `TokenValidator` mounted on the
    # runtime router alone.  One audience for both would mean a clinician's browser token was
    # accepted by the runtime endpoints and an agent's service token by the clinical ones -- two
    # principal classes behind one credential, which is exactly what §7 says an agent must not be.
    # The audience is the cheapest place to make that structural: a token minted for humans simply
    # does not validate here, and nothing has to remember to check.  Unset means the runtime
    # endpoints fail closed on every request, the same way the Exchange API does when its own OIDC
    # is unconfigured. A deployment that has not provisioned agent credentials has no agents, so
    # refusing every runtime call is the correct behaviour rather than a limitation.
    exchange_connect_runtime_audience: str | None = None
    # Keycloak realm role name (relayed via the standard `realm_access.roles` claim) that grants
    # DHealth platform-admin authority. Never inferred from an organization role -- see
    # exchange/authorization.py.
    exchange_platform_admin_role: str = "dhealth-platform-admin"
    # Deliberately separate from `audit_path` above (the OpenMRS/OpenELIS connector's own audit
    # log) -- DH-EX-002's audit events are a different closed schema with no `direction` field;
    # sharing one file would force a reader to distinguish two unrelated shapes by inspection.
    # `None` resolves to a sibling file next to `audit_path` (see `resolved_exchange_audit_path`).
    exchange_audit_path: Path | None = None
    # DH-EX-005: the opaque patient order-access grant's technical TTL -- an access-security
    # default, not a clinical policy. 168h (7 days) is a conservative MVP default; bounded to
    # [1, 720] hours (1 hour .. 30 days) so a misconfigured value cannot silently produce an
    # unbounded-lifetime or effectively-instant-expiry grant.
    exchange_patient_access_ttl_hours: int = 168
    # DH-NOTIFY-001 §10: patient SMS/WhatsApp notification. **Off by default**, and the default is
    # the decision -- a deployment that has not deliberately switched this on must never send a
    # message to a real phone number, and every development stack and test run inherits that.
    #
    # Three operationally distinct states, which §10 requires be distinguishable (see the
    # `notifications_*` properties below): *disabled* (nothing is owed, nothing is wrong),
    # *enabled and configured* (messages flow), and *enabled but incomplete* (a startup refusal,
    # never a service that looks deployed and silently sends nothing).
    notifications_enabled: bool = False
    notification_provider: str = "twilio"
    #: The public origin patient links are built from. **Explicit deployment configuration, never
    #: derived from an incoming `Host` header** (§17): a server-composed link built from an
    #: attacker-controlled header is a link that carries a live patient credential to an
    #: attacker-chosen origin. The browser-side QR/print path keeps using its own `window.origin`
    #: (`ui/src/features/patient/patientAccessUrl.ts`) and is unaffected.
    patient_public_base_url: str | None = None
    #: The exact URL Twilio is configured to call back, signed by them and validated against this
    #: value. `None` disables receipt handling entirely -- notifications then stop at `SENT`, which
    #: is honest rather than limiting.
    notification_status_callback_url: str | None = None
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    sms_from: str | None = None
    whatsapp_from: str | None = None
    #: ISO 3166-1 alpha-2 default region for parsing a destination typed without a country code.
    #: Unset by default, so a number with no country code is refused rather than guessed (§6).
    #: This is the configuration seam §6 asks for; Djibouti is deliberately not hard-coded.
    notification_default_region: str | None = None

    @classmethod
    def from_env(cls, environment: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if environment is None else environment
        timezone = env.get("TZ", "Africa/Djibouti")
        if timezone != "Africa/Djibouti":
            raise ConfigurationError("TZ must be Africa/Djibouti")

        db_name = _required(env, "ADAPTER_DB_NAME")
        db_user = _required(env, "ADAPTER_DB_USER")
        for name, value in (("ADAPTER_DB_NAME", db_name), ("ADAPTER_DB_USER", db_user)):
            if not _DB_IDENTIFIER.fullmatch(value):
                raise ConfigurationError(f"{name} is not a safe PostgreSQL identifier")

        return cls(
            timezone=timezone,
            db_host=_required(env, "ADAPTER_DB_HOST"),
            db_port=_positive_int(env, "ADAPTER_DB_PORT", 5432),
            db_name=db_name,
            db_user=db_user,
            db_password=_required(env, "ADAPTER_DB_PASSWORD"),
            audit_path=Path(env.get("ADAPTER_AUDIT_PATH", "/var/lib/daher/audit/audit.jsonl")),
            retry_max_attempts=_positive_int(env, "ADAPTER_RETRY_MAX_ATTEMPTS", 5),
            retry_multiplier_seconds=_positive_float(env, "ADAPTER_RETRY_MULTIPLIER_SECONDS", 1.0),
            retry_max_seconds=_positive_float(env, "ADAPTER_RETRY_MAX_SECONDS", 30.0),
            openelis_site_code=_optional_site_code(env, "OPENELIS_SITE_CODE"),
            omrs_base_url=env.get("ADAPTER_OMRS_BASE_URL", "").strip() or None,
            omrs_auth=env.get("ADAPTER_OMRS_AUTH", "").strip() or None,
            exchange_oidc_issuer=env.get("EXCHANGE_OIDC_ISSUER", "").strip() or None,
            exchange_oidc_audience=env.get("EXCHANGE_OIDC_AUDIENCE", "").strip() or None,
            exchange_oidc_jwks_url=env.get("EXCHANGE_OIDC_JWKS_URL", "").strip() or None,
            exchange_connect_runtime_audience=(
                env.get("EXCHANGE_CONNECT_RUNTIME_AUDIENCE", "").strip() or None
            ),
            exchange_platform_admin_role=(
                env.get("EXCHANGE_PLATFORM_ADMIN_ROLE", "").strip() or "dhealth-platform-admin"
            ),
            exchange_audit_path=(
                Path(env["EXCHANGE_AUDIT_PATH"])
                if env.get("EXCHANGE_AUDIT_PATH", "").strip()
                else None
            ),
            exchange_patient_access_ttl_hours=_bounded_int(
                env, "EXCHANGE_PATIENT_ACCESS_TTL_HOURS", 168, minimum=1, maximum=720
            ),
            **_notification_settings(env),
        )

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            username=self.db_user,
            password=self.db_password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )

    @property
    def resolved_exchange_audit_path(self) -> Path:
        if self.exchange_audit_path is not None:
            return self.exchange_audit_path
        return self.audit_path.with_name("exchange-" + self.audit_path.name)

    @property
    def exchange_oidc_configured(self) -> bool:
        """`False` means the Exchange API must fail closed on every request -- never a partial
        or best-effort validation. See `exchange/identity_auth.py`."""
        return bool(
            self.exchange_oidc_issuer
            and self.exchange_oidc_audience
            and self.exchange_oidc_jwks_url
        )

    @property
    def connect_runtime_oidc_configured(self) -> bool:
        """`False` means every DHealth Connect runtime endpoint must fail closed.

        Requires the Exchange's own OIDC *and* a distinct runtime audience. Distinct, checked here
        rather than trusted: setting both to the same string would silently reunite the two
        principal classes the second audience exists to separate, and it is the kind of
        configuration mistake that looks like it works -- agents authenticate, humans authenticate,
        and each can do the other's job.
        """
        return bool(
            self.exchange_oidc_configured
            and self.exchange_connect_runtime_audience
            and self.exchange_connect_runtime_audience != self.exchange_oidc_audience
        )

    @property
    def notification_receipts_configured(self) -> bool:
        """Whether an authenticated provider receipt can be accepted at all.

        Needs both the shared secret that signs it and the exact URL that was signed. Either one
        missing means the webhook refuses every request, and notifications truthfully stop at
        ``SENT`` -- a weaker claim, correctly made, rather than ``DELIVERED`` asserted on nothing.
        """
        return bool(self.twilio_auth_token and self.notification_status_callback_url)

    def build_notification_transport(self):  # noqa: ANN201 -- NotificationTransport | None
        """The configured provider, or ``None`` when sending is off.

        ``None`` is the *disabled* state, not a failure: callers treat it as "nothing to send" and
        report nothing wrong (§10). A misconfigured-but-enabled deployment never reaches here --
        ``from_env`` already refused to start.

        Imported lazily so that ``config`` keeps no import-time dependency on the notify package,
        matching how ``exchange/results.py`` and ``exchange/selection.py`` treat the connect
        boundary.
        """
        if not self.notifications_enabled:
            return None
        if self.notification_provider == "fake":
            # Reachable only by explicit configuration, and never in production: a deployment that
            # sets this has deliberately asked for a provider that contacts nothing. It exists so
            # the native demo stack can run the whole golden journey offline.
            from daher_adapter.notify.fake import FakeNotificationTransport

            return FakeNotificationTransport()

        from daher_adapter.notify.twilio import TwilioNotificationTransport

        return TwilioNotificationTransport(
            account_sid=self.twilio_account_sid or "",
            auth_token=self.twilio_auth_token or "",
            sms_from=self.sms_from,
            whatsapp_from=self.whatsapp_from,
            status_callback_url=self.notification_status_callback_url,
        )

    @property
    def service_request_delivery_ready(self) -> bool:
        """One of several required activation gates (§16) -- the only one a config value can
        represent. Patient-identifier policy, requester approval, test mapping, and the
        DH-011D correlation patch being live-proven are process/documentation gates, not
        environment values; they are recorded in PROJECT_STATE.md, not asserted here."""
        return self.openelis_site_code is not None
