"""DHEALTH-NATIVE-RECOVERY-001 — structural guarantees about the Native recovery tooling.

These assert properties of the files, not behaviour of a running deployment: the destructive drill
(`scripts/native/qualify-recovery.sh`) is what proves the behaviour, and it needs Docker, a real
browser and eight minutes. What is left over for a test suite is the set of promises that must not
quietly stop being true between drills -- an ordering in the restore script, a passphrase that must
never reach a Compose file, a historical backup that must keep working.

Text parsing rather than PyYAML for the same reason `test_native_deployment_topology.py` gives: the
adapter's runtime environment does not install it, and a test that cannot run is worth nothing. The
helpers are duplicated from that module deliberately -- a shared conftest fixture would make either
file's failure depend on the other's.
"""

from __future__ import annotations

import re
import stat
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
NATIVE = REPO / "docker-compose.native.yml"
NATIVE_DEMO = REPO / "docker-compose.native-demo.yml"
BACKUP_PAYLOAD = REPO / "deploy" / "native" / "backup" / "native-backup.sh"
RESTORE_PAYLOAD = REPO / "deploy" / "native" / "backup" / "native-restore.sh"
BACKUP_DOCKERFILE = REPO / "deploy" / "native" / "backup" / "Dockerfile"
HISTORICAL_BACKUP = REPO / "backup" / "backup.sh"
OPERATOR_SCRIPTS = (
    REPO / "scripts" / "native" / "_compose.sh",
    REPO / "scripts" / "native" / "backup.sh",
    REPO / "scripts" / "native" / "restore.sh",
    REPO / "scripts" / "native" / "recovery-sentinels.sh",
    REPO / "scripts" / "native" / "qualify-recovery.sh",
)

PASSPHRASE_VARIABLE = "BACKUP_ENCRYPTION_PASSPHRASE"
RECOVERY_SERVICES = ("native-backup", "native-restore")
#: Every component a restore must have. Named here as well as in the restore script, so a component
#: silently dropped from one of them is a failing test rather than a smaller archive.
REQUIRED_COMPONENTS = ("exchange.dump", "keycloak.dump", "adapter-audit.tar.gz")


def _directives(path: Path) -> str:
    return "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("#")
    )


def _code(path: Path) -> str:
    """A shell script's statements, without its comments.

    These scripts explain at length what they must NOT do -- the restore payload's header describes
    the `DROP DATABASE; pg_restore --no-privileges` defect the 2026-08-13 maternity drill found, and
    names `--no-owner` and `WITH (FORCE)` to say why neither is used. An assertion that those strings
    are absent has to read the code, or the explanation of a rule fails the test enforcing it.
    """
    return "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def _service_block(path: Path, service: str) -> str:
    text = _directives(path)
    start = text.index("\n  " + service + ":")
    rest = text[start + 1 :]
    following = re.search(r"\n  [A-Za-z0-9_-]+:", rest[1:])
    return rest[: following.start() + 1] if following else rest


# ---------------------------------------------------------------------------
# The passphrase
# ---------------------------------------------------------------------------
def test_no_compose_file_names_the_backup_passphrase() -> None:
    """Compose interpolates profile-gated services too, so a `${PASSPHRASE:?}` in the canonical file
    would make `docker compose config` fail for every operator who has never taken a backup -- and it
    would put the variable one `config` away from being printed. The wrappers pass it by name at run
    time instead."""
    for path in (NATIVE, NATIVE_DEMO, REPO / "docker-compose.yml"):
        text = path.read_text(encoding="utf-8")
        if path.name == "docker-compose.yml":
            # The historical sidecar legitimately consumes it; what matters there is that no value
            # is written down.
            for line in text.splitlines():
                if PASSPHRASE_VARIABLE in line and not line.strip().startswith("#"):
                    assert re.search(
                        rf"\$\{{{PASSPHRASE_VARIABLE}(:[-?][^}}]*)?\}}", line
                    ), f"{path.name} appears to assign a passphrase value: {line.strip()}"
            continue
        assert PASSPHRASE_VARIABLE not in _directives(path), (
            f"{path.name} names {PASSPHRASE_VARIABLE}; the Native recovery wrappers pass it by "
            "name at run time so that it never reaches a Compose file"
        )


def test_the_backup_payload_requires_the_passphrase_at_runtime() -> None:
    body = BACKUP_PAYLOAD.read_text(encoding="utf-8")
    assert f': "${{{PASSPHRASE_VARIABLE}:?' in body, (
        "the backup must refuse to run without a passphrase; an unencrypted archive is not a "
        "fallback worth having"
    )


def test_the_passphrase_never_reaches_a_command_line() -> None:
    """`-pass env:VAR`, never `-pass pass:...`: the second would put the secret in the process
    table, where any other process on the host can read it."""
    for path in (BACKUP_PAYLOAD, RESTORE_PAYLOAD):
        body = path.read_text(encoding="utf-8")
        assert "-pass env:" in body, f"{path.name} does not pass the passphrase by environment"
        assert "-pass pass:" not in body, f"{path.name} puts a passphrase on a command line"


# ---------------------------------------------------------------------------
# The recovery services
# ---------------------------------------------------------------------------
def test_the_recovery_services_cannot_be_started_by_an_ordinary_up() -> None:
    native = _directives(NATIVE)
    for service in RECOVERY_SERVICES:
        block = _service_block(NATIVE, service)
        assert 'profiles: ["recovery"]' in block, (
            f"{service} is not profile-gated; a plain `docker compose up` would start it"
        )
    # And nothing pulls them in through the back door.
    for service in RECOVERY_SERVICES:
        depends = re.findall(rf"^\s+{re.escape(service)}:\s*$", native, flags=re.MULTILINE)
        # The service's own definition line is one match; a `depends_on` entry would be another.
        assert len(depends) == 1, f"something in the graph depends on {service}"


def test_the_archive_destination_is_a_bind_mount_not_a_named_volume() -> None:
    """An archive kept in a named volume of the project it protects dies with that project. The
    destructive drill's own step 9 (`down -v`) is the proof of why."""
    for service in RECOVERY_SERVICES:
        block = _service_block(NATIVE, service)
        mount = re.search(r"- (\S+):/backup(:ro)?$", block, flags=re.MULTILINE)
        assert mount, f"{service} does not mount a backup destination"
        source = mount.group(1)
        assert source.startswith("${NATIVE_BACKUP_DIR"), (
            f"{service}'s /backup source is {source}; it must be an operator-supplied path"
        )
        assert "./" in source or "/" in source, "the default must be a filesystem path"


def test_the_backup_reads_the_audit_volume_read_only() -> None:
    block = _service_block(NATIVE, "native-backup")
    assert "- adapter-audit:/audit:ro" in block, (
        "a backup has no business being able to write to the audit trail it copies"
    )


def test_the_recovery_services_need_nothing_from_the_reference_stack() -> None:
    for service in RECOVERY_SERVICES:
        block = _service_block(NATIVE, service).lower()
        for marker in ("omrs_", "oe_", "openmrs", "openelis", "mariadb", "clinlims"):
            assert marker not in block, f"{service} names {marker}"
    for path in (BACKUP_PAYLOAD, RESTORE_PAYLOAD):
        body = _directives(path).lower()
        for marker in ("omrs_db_root_password", "oe_postgres_password", "clinlims", "mariadb"):
            assert marker not in body, f"{path.name} names {marker}; Native recovery is Native-only"


# ---------------------------------------------------------------------------
# The archive contract
# ---------------------------------------------------------------------------
def test_the_backup_and_restore_agree_on_one_explicit_format_version() -> None:
    backup = BACKUP_PAYLOAD.read_text(encoding="utf-8")
    restore = RESTORE_PAYLOAD.read_text(encoding="utf-8")
    written = re.search(r"^FORMAT='([^']+)'", backup, flags=re.MULTILINE)
    accepted = re.search(r"^FORMAT='([^']+)'", restore, flags=re.MULTILINE)
    assert written and accepted, "both sides must declare an archive format"
    assert written.group(1) == accepted.group(1)
    assert 'unsupported backup format' in restore, (
        "an archive from an unknown writer must be refused, not guessed at"
    )


def test_the_restore_names_its_required_components_itself() -> None:
    """A manifest that only agrees with itself is not integrity. If the required set came from the
    manifest, a backup that forgot a component would restore happily -- which is NC-RECOVERY-1."""
    restore = RESTORE_PAYLOAD.read_text(encoding="utf-8")
    required = re.search(r"^REQUIRED_COMPONENTS='([^']+)'", restore, flags=re.MULTILINE)
    assert required, "the restore script must name the components it requires"
    assert set(required.group(1).split()) == set(REQUIRED_COMPONENTS)


def test_the_manifest_records_a_hash_and_a_size_for_every_component() -> None:
    backup = BACKUP_PAYLOAD.read_text(encoding="utf-8")
    assert "sha256sum" in backup
    assert re.search(r"printf 'component %s %s %s", backup), (
        "each manifest component line must carry a name, a byte size and a hash"
    )
    assert "alembic_revision=" in backup
    assert "created_utc=" in backup
    assert "format=" in backup


def test_the_manifest_carries_no_secret() -> None:
    """The manifest is the one part of the archive a future tool may want to read before deciding
    anything, so nothing in it may be a credential."""
    backup = BACKUP_PAYLOAD.read_text(encoding="utf-8")
    manifest = backup[backup.index('>"${WORK}/MANIFEST"') - 2000 : backup.index('>"${WORK}/MANIFEST"')]
    for forbidden in ("PASSWORD", "PASSPHRASE", "SECRET", "TOKEN"):
        assert forbidden not in manifest, f"the manifest block references {forbidden}"


# ---------------------------------------------------------------------------
# Fail-closed ordering
# ---------------------------------------------------------------------------
def test_every_integrity_check_happens_before_anything_can_be_dropped() -> None:
    """The ordering IS the safety property. NC-RECOVERY-2 (corrupt component) and NC-RECOVERY-3
    (wrong passphrase) both require that a failure leaves the target untouched, and that is only
    true if no statement that changes anything is issued until verification has passed."""
    restore = _code(RESTORE_PAYLOAD)

    # Call order, not text order. `restore_database` is *defined* near the top and contains the only
    # DROP in the file; what matters is when it is invoked. So the assertion is made against the
    # `restore)` branch -- the one execution path that can change anything.
    branch = restore[restore.index("  restore)") :]
    staged = branch.index("stage_and_verify ")
    announced = branch.index("APPLYING --")
    applied = branch.index("restore_database ")
    assert staged < announced < applied, (
        "the restore branch must verify, then announce, then apply. Any other order means a corrupt "
        "or wrong-passphrase archive could reach a DROP DATABASE before being rejected."
    )

    # And the only DROP in the whole script is inside that one function, so there is no second path
    # to a destructive statement.
    assert restore.count("DROP DATABASE") == 1
    assert restore.index("DROP DATABASE") < restore.index("  restore)"), (
        "the DROP belongs to the restore_database function, not to the entry branch"
    )


def test_the_restore_refuses_an_ambiguous_target() -> None:
    restore = RESTORE_PAYLOAD.read_text(encoding="utf-8")
    assert "--confirm-destroy-database" in restore
    assert 'Refusing an ambiguous target' in restore
    # The archive's own idea of which databases it holds is checked against the deployment, so a
    # demo archive cannot be restored onto production or the reverse.
    assert 'but this deployment is' in restore


def test_the_restore_will_not_drop_a_database_that_still_has_sessions() -> None:
    restore = _code(RESTORE_PAYLOAD)
    assert "pg_stat_activity" in restore
    assert "WITH (FORCE)" not in restore and "WITH FORCE" not in restore, (
        "a forced drop would kill a live Keycloak's connections instead of refusing"
    )


def test_the_restore_re_hardens_tenancy_and_then_proves_it() -> None:
    """The exact defect the 2026-08-13 maternity drill found: CREATE DATABASE grants PUBLIC a
    CONNECT privilege, so a restore that only drops and recreates silently un-hardens the
    deployment. NC-RECOVERY-4 removes this and the TCP probe must go red."""
    restore = RESTORE_PAYLOAD.read_text(encoding="utf-8")
    assert "REVOKE ALL PRIVILEGES ON DATABASE" in restore
    assert "verify_tenancy" in restore
    # Six cases, including both wrong-password denials.
    probes = restore.count("  probe ")
    assert probes >= 6, f"only {probes} tenancy probes; six cases are required"
    assert "wrong-password-not-the-real-one" in restore
    # Over TCP, with a password. A unix socket would prove nothing about tenancy.
    assert '-h "$PGHOST"' in restore


def test_the_restore_preserves_table_ownership() -> None:
    restore = _code(RESTORE_PAYLOAD)
    assert "--no-owner" not in restore, (
        "stripping ownership hands every table to the superuser and leaves the tenant role unable "
        "to write its own data"
    )
    assert "--exit-on-error" in restore
    assert "tableowner" in restore, "the restore must verify ownership rather than assume it"
    # Verified against the archive's own table of contents, not against "everything belongs to the
    # tenant role". The demo overlay's marker table is deliberately superuser-owned so the
    # application cannot write the fact that authorizes a destructive reset, and an ownership check
    # that did not read the archive would have required that property to be abandoned.
    assert "pg_restore --list" in restore
    assert "expected-owners.txt" in restore


def test_the_restore_extracts_only_an_allowlist_of_members() -> None:
    """Path-traversal safety by allowlist rather than by filtering: a member whose name is not
    exactly one of four expected basenames is refused before anything is extracted."""
    restore = RESTORE_PAYLOAD.read_text(encoding="utf-8")
    allowed = re.search(r"^ALLOWED_MEMBERS='([^']+)'", restore, flags=re.MULTILINE)
    assert allowed
    assert set(allowed.group(1).split()) == set(REQUIRED_COMPONENTS) | {"MANIFEST"}
    assert "unexpected member" in restore
    members_checked = restore.index("ALLOWED_MEMBERS")
    extracted = restore.index("tar -xzf")
    assert members_checked < extracted


def test_the_restore_refuses_a_revision_this_codebase_does_not_know() -> None:
    restore = RESTORE_PAYLOAD.read_text(encoding="utf-8")
    assert "MIGRATIONS_DIR" in restore
    assert "is not mounted" in restore, "an unmounted migration directory must refuse, not skip"


# ---------------------------------------------------------------------------
# Hygiene
# ---------------------------------------------------------------------------
def test_both_payloads_restrict_permissions_and_clean_up() -> None:
    for path in (BACKUP_PAYLOAD, RESTORE_PAYLOAD):
        body = path.read_text(encoding="utf-8")
        assert "umask 077" in body, f"{path.name} does not restrict the permissions it creates"
        assert "trap cleanup EXIT" in body, f"{path.name} does not clean up on every exit path"


def test_the_staging_area_is_a_tmpfs_in_both_services() -> None:
    """Plaintext dumps must not reach a disk even transiently."""
    for service in RECOVERY_SERVICES:
        block = _service_block(NATIVE, service)
        assert "tmpfs:" in block and "/tmp:size=" in block
        staging = re.search(r"NATIVE_BACKUP_STAGING_DIR: (/tmp/\S+)", block)
        assert staging, f"{service} does not stage under the tmpfs mount"


def test_every_operator_script_is_executable_in_the_index() -> None:
    """A script this repository's own documentation tells an operator to run as `./script.sh` has to
    be committed executable. The exec bit not surviving a Windows checkout has bitten this repository
    before."""
    for path in (*OPERATOR_SCRIPTS, BACKUP_PAYLOAD, RESTORE_PAYLOAD):
        assert path.exists(), f"{path} is missing"
        mode = path.stat().st_mode
        assert mode & stat.S_IXUSR, f"{path.name} is not executable"


def test_the_drill_refuses_to_run_without_explicit_confirmation() -> None:
    drill = (REPO / "scripts" / "native" / "qualify-recovery.sh").read_text(encoding="utf-8")
    assert "--yes-destroy-this-deployment" in drill
    assert "NATIVE_RECOVERY_DRILL_ALLOW_PRODUCTION" in drill, (
        "the drill must refuse the production env file unless told twice"
    )


def test_the_drill_proves_absence_before_it_restores() -> None:
    """The false green this whole design is built against: a clean environment that recreates
    equivalent-looking records. `assert-absent` must run before the restore, and `assert-present`
    after it."""
    drill = (REPO / "scripts" / "native" / "qualify-recovery.sh").read_text(encoding="utf-8")
    absent = drill.index("assert-absent")
    restored = drill.index('restore.sh" restore')
    present = drill.index("assert-present")
    assert absent < restored < present


def test_the_drill_verifies_tenancy_before_bringing_the_stack_up() -> None:
    """`up` re-runs `dhealth-db-hardening`, which would mask a restore that failed to re-harden. The
    probe has to happen in between, or NC-RECOVERY-4 can never go red."""
    drill = (REPO / "scripts" / "native" / "qualify-recovery.sh").read_text(encoding="utf-8")
    restored = drill.index('restore.sh" restore')
    tenancy = drill.index("verify-tenancy")
    after_restore_up = drill.index("start the restored Native deployment")
    assert restored < tenancy < after_restore_up


# ---------------------------------------------------------------------------
# The historical track keeps working
# ---------------------------------------------------------------------------
def test_the_historical_backup_still_understands_its_own_deployment() -> None:
    """DHEALTH-NATIVE-RECOVERY-001 built a second implementation rather than bending this one. This
    asserts it was really left alone -- a Native change that reached in here would be a regression
    for the maternity/reference-connector track, which is real clinical software."""
    body = HISTORICAL_BACKUP.read_text(encoding="utf-8")
    for expected in (
        "mariadb-dump",
        "OMRS_DB_ROOT_PASSWORD",
        "OE_POSTGRES_PASSWORD",
        "clinlims",
        "openmrs-data",
    ):
        assert expected in body, f"the historical backup lost {expected}"
    assert "dhealth_exchange" not in body, (
        "the historical backup has acquired Native concepts; the two must stay separate"
    )


def test_the_native_backup_image_derives_from_the_deployments_own_postgres() -> None:
    """A pg_dump from a different major version than the server is one of the classic ways a backup
    turns out to be unrestorable."""
    dockerfile = BACKUP_DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r"^FROM postgres:16", dockerfile, flags=re.MULTILINE)
    native = _directives(NATIVE)
    assert "image: postgres:16-alpine" in native
    # openssl is not in the alpine image and the repository's encryption is an `openssl enc`
    # invocation, so it is installed at build time rather than needed at run time.
    assert "apk add --no-cache openssl" in dockerfile
