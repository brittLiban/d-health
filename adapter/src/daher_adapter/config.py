from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import URL

_DB_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# DH-NOTIFY-META-001 §3: `vNN.N`. Validated rather than trusted -- the Graph API version is the
# value most likely to be pasted from a stale example, and a malformed or unversioned path
# silently changes which API a deployment talks to.
_GRAPH_VERSION = re.compile(r"^v\d+\.\d+$")

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
#: silent fallback to a default: "I set DHEALTH_NOTIFICATION_PROVIDER=twilio and nothing happened"
#: must not be a thing that can occur. Twilio was removed by DH-NOTIFY-META-001/ADR-059 -- WhatsApp
#: goes directly to Meta, and no SMS provider has been selected.
_KNOWN_NOTIFICATION_PROVIDERS = frozenset({"meta", "fake"})

#: The two approved-template names a deployment must register in its WhatsApp Business Account.
#: Keys match `notify/templates.py`'s `MetaTemplateBinding.config_key`; DHealth never invents a
#: template name, because Meta approves them per WABA.
_META_TEMPLATE_KEYS = ("META_TEMPLATE_PATIENT_ACCESS_FR", "META_TEMPLATE_PATIENT_UPDATE_FR")


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
    """DH-NOTIFY-001 §10 / DH-NOTIFY-META-001 §4 — fail closed when sending is on but cannot work.

    The asymmetry is the point. With notifications **off**, every value below is optional and the
    adapter starts normally -- that is the state every test, every development stack and the native
    demo run in, and it must stay frictionless. With notifications **on**, a missing credential is a
    refusal to start, naming the variable, rather than a process that looks deployed and quietly
    sends nothing. §10 requires those two to be different states, and a half-configured service that
    boots green is precisely the shape that makes them look the same.

    Two that are easy to miss and are checked anyway:

    ``DHEALTH_PATIENT_PUBLIC_BASE_URL`` -- without it there is no way to compose a patient link at
    all, so enabling notifications without it produces an access-link message that cannot be built,
    discovered at the first patient handoff rather than at boot.

    ``META_GRAPH_API_VERSION`` -- **required, with no default**, and shape-validated.
    DH-NOTIFY-META-001
    §3 says to pin and document the version DHealth has actually qualified against; a default would
    be a guess, and this repository could not reach Meta's changelog to make an informed one. The
    deployment states which version it uses, and the operations read shows it back.
    """
    enabled = _boolean(environment, "DHEALTH_NOTIFICATIONS_ENABLED", False)
    provider = environment.get("DHEALTH_NOTIFICATION_PROVIDER", "").strip().lower() or "meta"
    if provider not in _KNOWN_NOTIFICATION_PROVIDERS:
        raise ConfigurationError(
            f"DHEALTH_NOTIFICATION_PROVIDER must be one of "
            f"{', '.join(sorted(_KNOWN_NOTIFICATION_PROVIDERS))}"
        )

    base_url = environment.get("DHEALTH_PATIENT_PUBLIC_BASE_URL", "").strip() or None
    region = environment.get("DHEALTH_NOTIFICATION_DEFAULT_REGION", "").strip().upper() or None

    access_token = environment.get("META_WHATSAPP_ACCESS_TOKEN", "").strip() or None
    phone_number_id = environment.get("META_WHATSAPP_PHONE_NUMBER_ID", "").strip() or None
    business_account_id = environment.get("META_WHATSAPP_BUSINESS_ACCOUNT_ID", "").strip() or None
    app_secret = environment.get("META_APP_SECRET", "").strip() or None
    verify_token = environment.get("META_WEBHOOK_VERIFY_TOKEN", "").strip() or None
    graph_version = environment.get("META_GRAPH_API_VERSION", "").strip() or None
    template_language = environment.get("META_TEMPLATE_LANGUAGE", "").strip() or None
    template_names = {
        key: value for key in _META_TEMPLATE_KEYS if (value := environment.get(key, "").strip())
    }

    if region is not None and (len(region) != 2 or not region.isalpha()):
        raise ConfigurationError(
            "DHEALTH_NOTIFICATION_DEFAULT_REGION must be an ISO 3166-1 alpha-2 code (e.g. DJ)"
        )
    if graph_version is not None and not _GRAPH_VERSION.fullmatch(graph_version):
        raise ConfigurationError("META_GRAPH_API_VERSION must look like 'v23.0'")

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
        if provider == "meta":
            if access_token is None:
                raise ConfigurationError(
                    "META_WHATSAPP_ACCESS_TOKEN is required when notifications are enabled"
                )
            if phone_number_id is None:
                raise ConfigurationError(
                    "META_WHATSAPP_PHONE_NUMBER_ID is required when notifications are enabled"
                )
            if graph_version is None:
                raise ConfigurationError(
                    "META_GRAPH_API_VERSION is required when notifications are enabled -- there "
                    "is deliberately no default; pin the version this deployment has qualified"
                )
            if template_language is None:
                raise ConfigurationError(
                    "META_TEMPLATE_LANGUAGE is required when notifications are enabled -- it must "
                    "match the language code of the templates approved in the WABA"
                )
            missing_templates = [key for key in _META_TEMPLATE_KEYS if key not in template_names]
            if missing_templates:
                raise ConfigurationError(
                    f"{', '.join(missing_templates)} is required when notifications are enabled -- "
                    "DHealth never invents an approved template name"
                )

    return {
        "notifications_enabled": enabled,
        "notification_provider": provider,
        "patient_public_base_url": base_url,
        "notification_default_region": region,
        "meta_access_token": access_token,
        "meta_phone_number_id": phone_number_id,
        "meta_business_account_id": business_account_id,
        "meta_app_secret": app_secret,
        "meta_webhook_verify_token": verify_token,
        "meta_graph_api_version": graph_version,
        "meta_template_language": template_language,
        "meta_template_names": template_names,
    }


#: The one deployment timezone this product runs in, and the only value `TZ` may hold.
#:
#: DHEALTH-APPOINTMENTS-001 gave this literal a name. It was already enforced below -- `from_env`
#: refuses any other `TZ` -- but appointment scheduling is the first feature that has to *interpret*
#: a wall-clock time rather than only stamp UTC instants, so a second component now needs the same
#: value. Naming it once is what keeps "do not hardcode the timezone" true: the scheduling code
#: imports this, and `exchange/api.py`'s router default is this constant rather than a second copy
#: of the string.
#:
#: **This is a deployment fact, not a facility setting.** Whether an `Organization` should carry
#: its own timezone is an open product question (ADR-073's Human Decision 9); until it is answered
#: every facility in a deployment shares this one, and `Africa/Djibouti` is UTC+3 with no DST.
DEPLOYMENT_TIMEZONE = "Africa/Djibouti"


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
    #: `meta` (the real WhatsApp Cloud API) or `fake` (contacts nothing). Twilio was removed by
    #: DH-NOTIFY-META-001/ADR-059: WhatsApp goes directly to Meta, and no SMS provider is selected.
    notification_provider: str = "meta"
    #: The public origin patient links are built from. **Explicit deployment configuration, never
    #: derived from an incoming `Host` header** (§17): a server-composed link built from an
    #: attacker-controlled header is a link that carries a live patient credential to an
    #: attacker-chosen origin. The browser-side QR/print path keeps using its own `window.origin`
    #: (`ui/src/features/patient/patientAccessUrl.ts`) and is unaffected.
    patient_public_base_url: str | None = None
    #: Meta WhatsApp Cloud API credentials. The access token is a bearer credential for the whole
    #: WhatsApp Business Account: it lives in the environment and in the transport's memory, and
    #: never in a log, an exception, a database row, a metric or an audit detail.
    meta_access_token: str | None = None
    meta_phone_number_id: str | None = None
    #: Provenance for the runbook, not used on the send path. Recorded so an operator can tell which
    #: WABA a deployment belongs to without opening Meta's console.
    meta_business_account_id: str | None = None
    #: Signs every inbound webhook (`X-Hub-Signature-256`). Without it the webhook refuses every
    #: request and notifications truthfully stop at `SENT` -- a weaker claim, correctly made.
    meta_app_secret: str | None = None
    #: Echoed back during Meta's subscription handshake. Required alongside the app secret.
    meta_webhook_verify_token: str | None = None
    #: **Required when sending is enabled, with no default** (§3). DHealth has qualified no Graph
    #: API version; the deployment states which one it uses and the operations read shows it back.
    meta_graph_api_version: str | None = None
    #: The language code of the templates approved in the WABA (e.g. `fr`). Must match exactly.
    meta_template_language: str | None = None
    #: Approved template names, keyed by the config variable that supplied them. DHealth never
    #: invents one -- Meta approves templates per WABA.
    meta_template_names: dict[str, str] = field(default_factory=dict)
    #: ISO 3166-1 alpha-2 default region for parsing a destination typed without a country code.
    #: Unset by default, so a number with no country code is refused rather than guessed (§6).
    #: This is the configuration seam §6 asks for; Djibouti is deliberately not hard-coded.
    notification_default_region: str | None = None

    @classmethod
    def from_env(cls, environment: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if environment is None else environment
        timezone = env.get("TZ", DEPLOYMENT_TIMEZONE)
        if timezone != DEPLOYMENT_TIMEZONE:
            raise ConfigurationError(f"TZ must be {DEPLOYMENT_TIMEZONE}")

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

        Needs both the app secret that signs an event and the verify token that registers the
        subscription. Either one missing means the webhook refuses every request, and notifications
        truthfully stop at ``SENT`` -- a weaker claim, correctly made, rather than ``DELIVERED``
        asserted on nothing.
        """
        return bool(self.meta_app_secret and self.meta_webhook_verify_token)

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

        from daher_adapter.notify.meta import MetaWhatsAppTransport

        return MetaWhatsAppTransport(
            access_token=self.meta_access_token or "",
            phone_number_id=self.meta_phone_number_id or "",
            graph_api_version=self.meta_graph_api_version or "",
            template_names=self.meta_template_names,
            template_language=self.meta_template_language or "",
            business_account_id=self.meta_business_account_id,
        )

    @property
    def service_request_delivery_ready(self) -> bool:
        """One of several required activation gates (§16) -- the only one a config value can
        represent. Patient-identifier policy, requester approval, test mapping, and the
        DH-011D correlation patch being live-proven are process/documentation gates, not
        environment values; they are recorded in PROJECT_STATE.md, not asserted here."""
        return self.openelis_site_code is not None
