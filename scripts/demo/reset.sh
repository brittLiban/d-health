#!/bin/bash
# DHealth Native Demo — reset (DH-DEMO-001)
#
# Restores the running demo to its one deterministic synthetic baseline: clears every workflow
# object a golden journey created (orders, results, releases, amendments, reviews, patient-access
# grants) and reconciles the baseline organizations/catalog/route/offering/template/patient/users
# back to exactly the expected state. Safe to run repeatedly -- see
# `adapter/src/daher_adapter/exchange/demo_seed.py`'s own docstring for the three independent
# facts it checks before touching anything, and its `_WORKFLOW_TABLES` list for exactly what gets
# cleared. Requires the demo to already be running (`scripts/demo/up.sh`) -- this script does not
# start it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

# DHEALTH-NATIVE-DEPLOY-001: the demo is an overlay on the canonical Native deployment, so every
# demo command names both files -- the production topology first, then the demo's own additions.
COMPOSE=(docker compose -f docker-compose.native.yml -f docker-compose.native-demo.yml --env-file demo/native/demo.env)

echo "Resetting the DHealth Native Demo to its deterministic baseline..."

# Re-converges the three demo Keycloak accounts first, including clearing any brute-force
# lockout a mistyped password left behind -- otherwise "reset" would restore every database fact
# but could leave a demo login still locked out, which is not a full reset from an operator's
# point of view. `run --rm`, never `up`: `up -d` returns 0 as soon as the container is started,
# regardless of its own exit code, which would let `set -e` silently miss a broken reconcile;
# `run` propagates the container's real exit code.
"${COMPOSE[@]}" run --rm --no-deps keycloak-demo-users

"${COMPOSE[@]}" exec -T adapter python -m daher_adapter.exchange.demo_seed reset
echo "Done."
