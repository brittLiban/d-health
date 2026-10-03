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
# This is the multi-arch **index** digest, not one platform's manifest. The difference is not
# cosmetic: a platform digest builds only on that architecture, so pinning linux/amd64 here would
# work on a GitHub runner and fail on an arm64 developer machine. The first attempt at this pin got
# that wrong -- `docker manifest inspect --verbose` reports one `Ref` per platform and the amd64
# entry is first, so `sha256:1a66d744...` (the amd64 manifest inside this very index) looked like
# the answer. `docker manifest inspect <ref>@<digest>` is what distinguishes them: it verifies
# against an index and answers "manifest verification failed" for a platform manifest.
#
# Provenance of this digest (DHEALTH-CI-REPAIR-001, 2026-10-04) -- resolved and pulled, not copied:
#   1. the registry's own `Docker-Content-Digest` for `postgres:16-alpine`, requested with the
#      index media types in `Accept`, is this value; the index lists eight platforms;
#   2. `docker pull postgres:16-alpine@sha256:721873c3...` succeeded, so it is retrievable rather
#      than merely well-formed -- AGENTS.md forbids recording a digest that has not been pulled;
#   3. `docker manifest inspect` on it returns `application/vnd.oci.image.index.v1+json`, which is
#      the check that it really is the index;
#   4. the pulled image was verified to be what the comment below claims: PostgreSQL 16.15 on
#      Alpine 3.24.2, with `pg_dump`/`pg_restore` 16.15 and **no** `openssl` binary.
#
# A digest already present on this machine was rejected on the way here: `docker image inspect`
# reported a local `RepoDigest` of `sha256:530a5243...`, which the registry would not verify at
# all -- consistent with the image having been side-loaded. Pinning it would have produced a
# reference CI could not pull.
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
FROM postgres:16-alpine@sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea

RUN apk add --no-cache openssl

COPY native-backup.sh /usr/local/bin/native-backup
COPY native-restore.sh /usr/local/bin/native-restore
RUN chmod 0755 /usr/local/bin/native-backup /usr/local/bin/native-restore

# No CMD: both entry points are explicit one-shots invoked by Compose, and a default that could
# start a backup because somebody ran the image bare is not a default worth having.
ENTRYPOINT []
CMD ["/bin/sh", "-c", "echo 'run native-backup or native-restore explicitly' >&2; exit 1"]
