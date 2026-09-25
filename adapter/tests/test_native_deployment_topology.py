"""DHEALTH-NATIVE-DEPLOY-001: what the canonical Native deployment is, and what it must never be.

`docker-compose.native.yml` is the first deployment topology that describes DHealth Native in its
own terms. Before it, the only standalone Native topology was the demo, which built one by
`extends:`-ing services out of `docker-compose.yml` -- and because Compose merges `depends_on` from
the base and offers no way to remove an entry, every extended service arrived carrying the
maternity track's dependency graph. The visible symptom was a PostgreSQL service called
`openelis-db`, aliased to `db.openelis.org`, in a deployment running no OpenELIS at all.

These are structural assertions on the committed files. They cannot prove the stack boots -- that
is what the clean-boot qualification recorded in `PROJECT_STATE.md` is for -- but they are what
stops the boundary eroding between boots, which is the failure mode that produced the inherited
names in the first place. Each is paired with a negative control recorded in that entry.

Read as text rather than through a YAML parser: PyYAML is not in this project's dependency set and
a deployment-separation ticket is not the place to add one. The shapes asserted below (top-level
service names, one environment value, the presence or absence of a word among the directives) are
unambiguous in files this repository owns and formats consistently.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
NATIVE = REPO / "docker-compose.native.yml"
NATIVE_DEMO = REPO / "docker-compose.native-demo.yml"
HISTORICAL = REPO / "docker-compose.yml"
NATIVE_CADDYFILE = REPO / "deploy" / "native" / "Caddyfile"
NATIVE_ENV_EXAMPLE = REPO / "deploy" / "native" / "native.env.example"

#: Every name belonging to the preserved reference/maternity track. A Native deployment that
#: mentions any of them has re-acquired the coupling this file exists to prevent.
LEGACY_MARKERS = ("openmrs", "openelis", "mariadb", "clinlims", "maternity")

#: The services the canonical Native deployment is, exactly. `native-backup`/`native-restore`
#: (DHEALTH-NATIVE-RECOVERY-001) are in this set because they are defined in this file, but they are
#: in the `recovery` profile and nothing depends on them, so no `up` can start them --
#: `test_the_recovery_services_cannot_be_started_by_an_ordinary_up` is what holds that.
EXPECTED_NATIVE_SERVICES = {
    "dhealth-db",
    "dhealth-db-hardening",
    "keycloak",
    "keycloak-config",
    "adapter",
    "daher-ui",
    "caddy",
    "native-backup",
    "native-restore",
}

#: The two that must never run unless an operator asks for them by name.
RECOVERY_SERVICES = ("native-backup", "native-restore")


def _directives(path: Path) -> str:
    """The file's instructions, without its comments.

    These files explain their own history at length, and naming OpenELIS in a comment that says
    why OpenELIS is *not* here must not fail the check asserting OpenELIS is not here.
    """
    return "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("#")
    )


def _service_names(path: Path) -> set[str]:
    """Top-level service names: the keys indented exactly two spaces under `services:`."""
    names: set[str] = set()
    inside = False
    for line in _directives(path).splitlines():
        if line.startswith("services:"):
            inside = True
            continue
        if not inside:
            continue
        if line and not line.startswith(" "):
            break
        match = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if match:
            names.add(match.group(1))
    return names


def _service_block(path: Path, service: str) -> str:
    """One service's own lines, up to the next key at the same indent."""
    text = _directives(path)
    start = text.index("\n  " + service + ":")
    rest = text[start + 1 :]
    following = re.search(r"\n  [A-Za-z0-9_-]+:", rest[1:])
    return rest[: following.start() + 1] if following else rest


def test_the_native_deployment_is_exactly_the_services_native_needs() -> None:
    assert _service_names(NATIVE) == EXPECTED_NATIVE_SERVICES


def test_the_native_deployment_names_no_legacy_service_image_or_hostname() -> None:
    """NC-NATIVE-DEPLOY-1 and NC-NATIVE-DEPLOY-2 both land here.

    One assertion covers both because they are the same defect at different depths: a service, an
    image, a `depends_on` entry and a hostname alias are all ways of saying "Native needs the
    reference stack", and the whole point is that it does not.
    """
    found = [marker for marker in LEGACY_MARKERS if marker in _directives(NATIVE).lower()]
    assert found == [], (
        f"docker-compose.native.yml refers to {found} -- the Native deployment must not require, "
        "or be named after, the OpenMRS/OpenELIS reference stack"
    )


def test_the_native_deployment_does_not_extend_the_historical_compose_file() -> None:
    """`extends:` is how the coupling arrived: Compose merges `depends_on` from the base file and
    a local override cannot remove an entry, so extending the historical file means inheriting its
    dependency graph whatever the override says."""
    assert "extends" not in _directives(NATIVE)


def test_every_native_service_reaches_the_database_by_its_real_name() -> None:
    """The database is `dhealth-db` in the URL Keycloak connects with and in the host the adapter
    is given -- never a compatibility alias for a product that is not running."""
    keycloak = _service_block(NATIVE, "keycloak")
    assert "jdbc:postgresql://dhealth-db:5432/" in keycloak

    adapter = _service_block(NATIVE, "adapter")
    assert "ADAPTER_DB_HOST: dhealth-db" in adapter

    database = _service_block(NATIVE, "dhealth-db")
    assert "aliases" not in database, "the database answers to its own name and to no alias"


def test_the_native_deployment_never_sets_the_demo_seed_authority() -> None:
    """NC-NATIVE-DEPLOY-3.

    `demo_seed.py` requires three independent facts before it will clear a row: the database's own
    name, a marker table only the demo's db-init writes, and `ADAPTER_DEMO_ENVIRONMENT=1`. The
    production topology satisfies none of them, and this asserts all three.
    """
    directives = _directives(NATIVE)
    assert "ADAPTER_DEMO_ENVIRONMENT" not in directives
    assert "mark-demo-environment" not in directives
    assert "dhealth_native_demo" not in directives


def test_the_demo_overlay_is_the_only_place_demo_authority_is_granted() -> None:
    """The other half of NC-NATIVE-DEPLOY-3: the demo still works, and it is the only file in the
    repository that can switch the seed's authority on."""
    demo = _directives(NATIVE_DEMO)
    assert "ADAPTER_DEMO_ENVIRONMENT" in demo
    assert "mark-demo-environment" in demo

    granting = sorted(
        path.name
        for path in REPO.glob("docker-compose*.yml")
        if "ADAPTER_DEMO_ENVIRONMENT" in _directives(path)
    )
    assert granting == ["docker-compose.native-demo.yml"], (
        f"{granting} grant demo-seed authority; only the demo overlay may"
    )


def test_the_demo_is_an_overlay_rather_than_a_second_topology() -> None:
    """It no longer defines a database, a Keycloak or an edge of its own: it adds users, demo
    credentials, the demo database name and the seed authority on top of the production file."""
    names = _service_names(NATIVE_DEMO)
    assert "keycloak-demo-users" in names
    assert names <= EXPECTED_NATIVE_SERVICES | {"keycloak-demo-users"}
    for name in names - {"keycloak-demo-users"}:
        assert "image:" not in _service_block(NATIVE_DEMO, name), (
            f"{name} redefines the production service instead of adding to it"
        )


def test_required_production_secrets_fail_closed() -> None:
    """A deployment that starts anyway, on a value nobody chose, is the failure this prevents.
    Compose's `:?` refuses to render the file at all."""
    directives = _directives(NATIVE)
    for name in (
        "BASE_DOMAIN",
        "KEYCLOAK_PUBLIC_URL",
        "POSTGRES_SUPERUSER_PASSWORD",
        "KEYCLOAK_DB_PASSWORD",
        "ADAPTER_DB_PASSWORD",
        "KEYCLOAK_ADMIN_PASSWORD",
        "KEYCLOAK_EXCHANGE_API_CLIENT_SECRET",
    ):
        assert re.search(r"\$\{" + name + r":\?", directives), f"{name} has no fail-closed default"


def test_the_native_env_contract_asks_for_nothing_from_the_reference_stack() -> None:
    """An operator deploying only Native should not have to work out which of the historical
    stack's forty-odd variables matter. In this file, all of them do."""
    names = [
        line.split("=", 1)[0]
        for line in NATIVE_ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.startswith("#")
    ]
    assert names, "the Native env contract defines no variables"
    for name in names:
        assert not name.startswith(("OMRS_", "OE_")), f"{name} belongs to the reference deployment"
    assert "ADAPTER_DEMO_ENVIRONMENT" not in names


def test_the_public_edge_publishes_exactly_the_native_surfaces() -> None:
    """Re-derived from the file rather than assumed: three vhosts, one Meta callback path, a 404
    catch-all on the API origin, and no health endpoint anywhere."""
    caddyfile = _directives(NATIVE_CADDYFILE)
    hosts = set(re.findall(r"^(\w+)\.\{\$BASE_DOMAIN\}", caddyfile, flags=re.MULTILINE))
    assert hosts == {"app", "id", "exchange"}

    handled = re.findall(r"^\s*handle\s+(\S+)\s*\{", caddyfile, flags=re.MULTILINE)
    assert "/notifications/v1/meta/webhook" in handled
    assert "/notifications/v1/*" not in handled
    for path in handled:
        assert not path.startswith("/health"), f"{path} publishes an internal health endpoint"
    assert re.search(r"handle\s*\{\s*respond 404", caddyfile)


def test_the_historical_deployment_is_untouched_and_still_complete() -> None:
    """This ticket separates Native out; it does not remove the reference/maternity track. That
    deployment must still describe its whole self."""
    historical = _service_names(HISTORICAL)
    for name in ("openmrs-backend", "openelis-webapp", "openmrs-db", "openelis-db"):
        assert name in historical, f"the preserved reference deployment lost {name}"
