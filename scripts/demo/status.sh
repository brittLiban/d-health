#!/bin/bash
# DHealth Native Demo — status (DH-DEMO-001)
#
# A truthful health summary, never fabricated: every line below reflects a real
# `docker compose ps`/`alembic current` read taken right now, not an assumption. Prints demo
# login usernames only -- never a password, matching every other status output in this
# repository (see backup/README.md's own convention).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

# DHEALTH-NATIVE-DEPLOY-001: the demo is an overlay on the canonical Native deployment, so every
# demo command names both files -- the production topology first, then the demo's own additions.
COMPOSE=(docker compose -f docker-compose.native.yml -f docker-compose.native-demo.yml --env-file demo/native/demo.env)

set -a
# shellcheck disable=SC1091
source demo/native/demo.env
set +a

echo "DHealth Native Demo"
echo

"${COMPOSE[@]}" ps --format 'table {{.Service}}\t{{.State}}\t{{.Health}}'
echo

if migration_output=$("${COMPOSE[@]}" exec -T adapter alembic current 2>/dev/null); then
    echo "Migration head:  ${migration_output}"
else
    echo "Migration head:  could not read (is the adapter service running? try scripts/demo/up.sh)"
fi
echo

echo "Demo logins (usernames only -- passwords are in demo/native/demo.env):"
echo "  ${DEMO_CLINICIAN_USERNAME}  (clinic)"
echo "  ${DEMO_LAB_TECH_USERNAME}   (lab technician)"
echo "  ${DEMO_RELEASER_USERNAME}   (result releaser)"
# DHEALTH-NATIVE-ACCESS-001: one facility administrator per organization, each holding ORG_ADMIN and
# no clinical role -- so each lands directly on Équipe et accès, and neither can administer the
# other's establishment.
echo "  ${DEMO_ADMIN_USERNAME:-demo-admin}      (clinic — Équipe et accès)"
echo "  ${DEMO_LAB_ADMIN_USERNAME:-demo-lab-admin}  (lab — Équipe et accès)"
echo

echo "Open: https://app.${BASE_DOMAIN}   (DHealth Clinic / DHealth Lab)"
