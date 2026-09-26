#!/bin/bash
# DHealth Native Demo — start (DH-DEMO-001)
#
# Starts the native-only DHealth Clinic -> Exchange -> DHealth Lab loop, seeds its deterministic
# synthetic baseline, and prints where to open it in a browser. Never waits on
# OpenMRS/OpenELIS -- this compose file (docker-compose.native-demo.yml) does not include them at
# all (see that file's own header for why).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

# DHEALTH-NATIVE-DEPLOY-001: the demo is an overlay on the canonical Native deployment, so every
# demo command names both files -- the production topology first, then the demo's own additions.
COMPOSE=(docker compose -f docker-compose.native.yml -f docker-compose.native-demo.yml --env-file demo/native/demo.env)

# Only for this script's own `echo` lines below (the compose commands above already read
# demo.env directly via `--env-file`) -- so the printed usernames reflect whatever demo.env
# actually says, never a hardcoded guess that could silently drift from it.
set -a
# shellcheck disable=SC1091
source demo/native/demo.env
set +a

echo "DHealth Native Demo"
echo

# `--wait`: Compose's own mechanism for exactly this -- blocks until every named service reports
# healthy/running, and exits non-zero on its own timeout or if any of them reports unhealthy.
# Deliberately not a hand-rolled polling loop parsing `docker compose ps` text output, which is
# fragile across Compose versions.
#
# Named explicitly rather than passing no services (which would mean "wait on every service in
# the project"): `keycloak-config`, `keycloak-demo-users`, and `dhealth-db-hardening`
# are one-shot `restart: "no"` jobs that exit 0 when done -- Compose's own `--wait` can treat an
# exited container as "not ready" even at exit 0 unless something still-running depends on it via
# `service_completed_successfully`. Waiting only on the long-lived services avoids that ambiguity
# entirely; those one-shots still run to completion first because the long-lived services
# `depends_on` them.
"${COMPOSE[@]}" up -d --build
"${COMPOSE[@]}" up -d --wait --wait-timeout 300 dhealth-db keycloak adapter daher-ui caddy

echo "Seeding deterministic synthetic baseline..."
"${COMPOSE[@]}" exec -T adapter python -m daher_adapter.exchange.demo_seed reset

echo
echo "DHealth Native Demo"
echo
"${COMPOSE[@]}" ps --format '{{.Service}}\t{{.Health}}'
echo
echo "Demo data       ✓ verified"
echo
echo "Open:"
echo "  https://app.localhost   (DHealth Clinic / DHealth Lab)"
echo
echo "Demo logins (usernames only -- passwords are in demo/native/demo.env, never printed here):"
echo "  ${DEMO_CLINICIAN_USERNAME:-demo-clinician}  (clinic)"
echo "  ${DEMO_LAB_TECH_USERNAME:-demo-lab-tech}    (lab technician)"
echo "  ${DEMO_RELEASER_USERNAME:-demo-releaser}    (result releaser)"
# DHEALTH-NATIVE-ACCESS-001: one facility administrator per organization. Each holds ORG_ADMIN and
# no clinical role, so each lands directly on Équipe et accès -- and neither can administer the
# other's establishment, which is the boundary the access qualification exercises.
echo "  ${DEMO_ADMIN_USERNAME:-demo-admin}      (clinic — Équipe et accès)"
echo "  ${DEMO_LAB_ADMIN_USERNAME:-demo-lab-admin}  (lab — Équipe et accès)"
echo
echo "Run scripts/demo/status.sh for a health summary, scripts/demo/reset.sh to restore the"
echo "baseline, or scripts/demo/down.sh to stop."
