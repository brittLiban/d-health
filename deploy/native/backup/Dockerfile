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
FROM postgres:16-alpine
# TODO(DHEALTH-NATIVE-RECOVERY-001): inherits docker-compose.native.yml's unpinned
# postgres:16-alpine. Pin both together from a machine that can reach Docker Hub's blob CDN;
# AGENTS.md forbids recording a digest that has not been pulled.

RUN apk add --no-cache openssl

COPY native-backup.sh /usr/local/bin/native-backup
COPY native-restore.sh /usr/local/bin/native-restore
RUN chmod 0755 /usr/local/bin/native-backup /usr/local/bin/native-restore

# No CMD: both entry points are explicit one-shots invoked by Compose, and a default that could
# start a backup because somebody ran the image bare is not a default worth having.
ENTRYPOINT []
CMD ["/bin/sh", "-c", "echo 'run native-backup or native-restore explicitly' >&2; exit 1"]
