# DHealth Native backup/restore tooling image (DHEALTH-NATIVE-RECOVERY-001).
#
# Built on the same PostgreSQL image the deployment itself runs, so `pg_dump`/`pg_restore` can never
# be a different major version from the server they are pointed at -- a mismatch there is one of the
# classic ways a backup turns out to be unrestorable at the worst possible moment.
#
# `postgres:16-alpine` ships no `openssl` binary (verified, not assumed), and this repository's
# backup encryption is an `openssl enc` invocation established by ADR-025. Installing it at build
# time is deliberate: a disaster-recovery tool must not need a package repository to be reachable at
# the moment it runs.
#
# The historical `backup/Dockerfile` is the precedent for this shape and is untouched -- it derives
# from postgres:14.4 and adds a MariaDB client, awscli and jq for the maternity track. This image
# adds none of those, because Native recovery needs none of them.
# Tag plus immutable digest, the convention used for every other external image here: the tag
# says what a human should read, the digest says what actually gets built.
#
# Provenance of this digest (DHEALTH-CI-REPAIR-001, 2026-10-04) -- it was resolved and pulled,
# not copied from anywhere:
#   1. `docker manifest inspect postgres:16-alpine` resolved the tag to this digest;
#   2. `docker pull postgres:16-alpine@sha256:1a66d744...` succeeded, so the digest is real and
#      retrievable rather than merely well-formed -- AGENTS.md forbids recording one that has
#      not been pulled, and this one has been;
#   3. the pulled image was then verified to be what the comment below claims: PostgreSQL 16.15
#      on Alpine 3.24.2, with `pg_dump`/`pg_restore` 16.15 and **no** `openssl` binary.
#
# The earlier TODO here said this could not be pinned from a machine unable to reach Docker
# Hub's blob CDN. That constraint no longer applied; the pull above is the evidence.
#
# `docker-compose.native.yml` still names `postgres:16-alpine` unpinned. It is deliberately not
# changed by this ticket: the repository's image-pin gate does not cover that file, and pinning
# the server image is a deployment decision about when the database moves, not CI maintenance.
# Recorded as a follow-up instead. Note the consequence while both exist: this tool image is
# pinned to 16.15 and the server tracks whatever `16-alpine` currently is, so the
# "never a different major version" guarantee below holds, but the minor versions can drift.
FROM postgres:16-alpine@sha256:1a66d744c1b459e13b05a8fca341da84cb63383e99ce262210efee5a319d4551

RUN apk add --no-cache openssl

COPY native-backup.sh /usr/local/bin/native-backup
COPY native-restore.sh /usr/local/bin/native-restore
RUN chmod 0755 /usr/local/bin/native-backup /usr/local/bin/native-restore

# No CMD: both entry points are explicit one-shots invoked by Compose, and a default that could
# start a backup because somebody ran the image bare is not a default worth having.
ENTRYPOINT []
CMD ["/bin/sh", "-c", "echo 'run native-backup or native-restore explicitly' >&2; exit 1"]
