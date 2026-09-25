#!/bin/bash
# DHealth Native Demo — stop (DH-DEMO-001)
#
# Plain `down.sh` stops containers, keeping the demo's own volumes (its Postgres data, its Caddy
# TLS state) -- the same "stop is not delete" convention the real docker-compose.yml's own
# `docker compose down` already has. `down.sh -v` additionally removes those volumes -- explicit,
# never the default, matching this ticket's own "do not make a normal down unexpectedly delete
# demo data" requirement. Either way, this can only ever touch THIS demo's own project
# (`dhealth-native-demo` -- see docker-compose.native-demo.yml's `name:`), never the maternity/
# reference-connector stack's own containers/volumes.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

# DHEALTH-NATIVE-DEPLOY-001: the demo is an overlay on the canonical Native deployment, so every
# demo command names both files -- the production topology first, then the demo's own additions.
COMPOSE=(docker compose -f docker-compose.native.yml -f docker-compose.native-demo.yml --env-file demo/native/demo.env)

if [[ "${1:-}" == "-v" ]]; then
    echo "Stopping the DHealth Native Demo AND deleting its volumes (Postgres data, Caddy TLS state)..."
    "${COMPOSE[@]}" down -v
else
    echo "Stopping the DHealth Native Demo (volumes kept -- use 'down.sh -v' to also delete them)..."
    "${COMPOSE[@]}" down
fi
