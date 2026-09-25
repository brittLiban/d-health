#!/bin/bash
# DHealth Native — recovery sentinels (DHEALTH-NATIVE-RECOVERY-001).
#
# The defence against the most plausible false green in a recovery drill: a clean environment that
# recreates equivalent-looking records and a test that cannot tell the difference. The demo baseline
# seeds organisations and a patient with FIXED NAMES, so "a patient called Patient Démo Alpha is on
# screen" proves nothing at all after a restore. These sentinels are therefore identifiers, not
# names, and they are asserted three times:
#
#   capture         after the source journey, before the backup
#   assert-absent   on the clean target, before the restore   -- must find NONE of them
#   assert-present  after the restore                          -- must find ALL of them, unchanged
#
#   scripts/native/recovery-sentinels.sh capture <out.json>
#   scripts/native/recovery-sentinels.sh assert-absent <in.json>
#   scripts/native/recovery-sentinels.sh assert-present <in.json>
#
# Reading identifiers with SQL is fine; the state itself was created by clicking the real product.
# The release fingerprint below is computed here rather than stored by the application, so that
# "the released content was not rewritten" is checked against evidence taken before the backup.
set -euo pipefail

# shellcheck source=scripts/native/_compose.sh
source "$(dirname "${BASH_SOURCE[0]}")/_compose.sh"

COMMAND="${1:-}"
FILE="${2:-}"
[ -n "$COMMAND" ] && [ -n "$FILE" ] || {
  sed -n '14,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
  exit 1
}

# Read the Exchange database name out of the env file the deployment is actually using, so the
# sentinels can never be read from a different database than the one being qualified.
EXCHANGE_DB="$(grep -E '^ADAPTER_DB_NAME=' "$NATIVE_ENV_FILE" | tail -1 | cut -d= -f2- || true)"
EXCHANGE_DB="${EXCHANGE_DB:-dhealth_exchange}"

psql_exchange() {
  "${NATIVE_COMPOSE[@]}" exec -T dhealth-db psql -X -At -U postgres -d "$EXCHANGE_DB" "$@"
}
psql_keycloak() {
  KC_DB="$(grep -E '^KEYCLOAK_DB_NAME=' "$NATIVE_ENV_FILE" | tail -1 | cut -d= -f2- || true)"
  "${NATIVE_COMPOSE[@]}" exec -T dhealth-db psql -X -At -U postgres -d "${KC_DB:-keycloak}" "$@"
}

# The most recently reviewed released order, with every identifier that recovery must return and a
# fingerprint of the released content. `json_build_object` rather than hand-assembled text so a NULL
# is visibly null instead of an empty string that reads like a value.
CAPTURE_SQL=$(cat <<'SQL'
WITH reviewed AS (
  SELECT r.id            AS review_id,
         r.status        AS review_status,
         r.reviewed_at   AS reviewed_at,
         r.lab_order_id  AS order_id,
         r.release_version_id
    FROM exchange_doctor_reviews r
   WHERE r.release_version_id IS NOT NULL
     AND r.reviewed_at IS NOT NULL
   ORDER BY r.reviewed_at DESC
   LIMIT 1
),
content AS (
  -- Everything the release VERSION actually binds, in a stable order. This is the fingerprint the
  -- released result must still hash to after recovery: a restore that silently rewrote a value, or
  -- bound the version to a different observation, changes it.
  SELECT rv.id AS release_version_id,
         encode(sha256(convert_to(string_agg(
           coalesce(o.source_code, '') || '|' ||
           coalesce(o.numeric_value::text, '') || '|' ||
           coalesce(o.numeric_unit, '') || '|' ||
           coalesce(o.text_value, '') || '|' ||
           coalesce(o.display_label_snapshot, ''),
           E'\n' ORDER BY o.source_code, o.id
         ), 'UTF8')), 'hex') AS fingerprint,
         count(*) AS observation_count
    FROM reviewed rd
    JOIN exchange_release_versions rv ON rv.id = rd.release_version_id
    JOIN exchange_release_manifest_entries me ON me.release_version_id = rv.id
    JOIN exchange_lab_observations o ON o.id = me.lab_observation_id
   GROUP BY rv.id
)
SELECT json_build_object(
  'order_id',               rd.order_id,
  'order_reference_ui',     left(rd.order_id::text, 8),
  'review_id',              rd.review_id,
  'review_status',          rd.review_status,
  'release_version_id',     rv.id,
  'release_version_number', rv.version_number,
  'lab_result_release_id',  rv.lab_result_release_id,
  'patient_id',             p.id,
  'patient_reference_id',   lo.patient_reference_id,
  'provider_organization_id', lo.provider_organization_id,
  'lab_organization_id',    ls.lab_organization_id,
  'release_fingerprint',    c.fingerprint,
  'observation_count',      c.observation_count,
  'counts', json_build_object(
    'organizations',    (SELECT count(*) FROM exchange_organizations),
    'patients',         (SELECT count(*) FROM exchange_patients),
    'orders',           (SELECT count(*) FROM exchange_lab_orders),
    'release_versions', (SELECT count(*) FROM exchange_release_versions),
    'reviews_done',     (SELECT count(*) FROM exchange_doctor_reviews WHERE reviewed_at IS NOT NULL),
    'observations',     (SELECT count(*) FROM exchange_lab_observations),
    'security_audit',   (SELECT count(*) FROM exchange_security_audit_events),
    'identities',       (SELECT count(*) FROM exchange_identities),
    'memberships',      (SELECT count(*) FROM exchange_organization_memberships),
    'patient_access_grants', (SELECT count(*) FROM exchange_patient_access_grants),
    'notifications',    (SELECT count(*) FROM patient_notifications),
    'connector_installations', (SELECT count(*) FROM connector_installations)
  ),
  -- Keycloak subjects the Exchange side believes in. After a restore these must be the SAME subject
  -- values: a clean demo target mints new ones, so a match is proof of restored identity state and
  -- not of a reconciler having run again.
  'identity_subs', (SELECT json_agg(i.subject ORDER BY i.subject) FROM exchange_identities i),
  'security_audit_latest_id', (SELECT e.id FROM exchange_security_audit_events e
                                ORDER BY e.created_at DESC LIMIT 1)
) AS sentinels
  FROM reviewed rd
  JOIN exchange_release_versions rv ON rv.id = rd.release_version_id
  JOIN content c ON c.release_version_id = rv.id
  JOIN exchange_lab_orders lo ON lo.id = rd.order_id
  JOIN exchange_patient_references pr ON pr.id = lo.patient_reference_id
  JOIN exchange_patients p ON p.id = pr.patient_id
  LEFT JOIN exchange_lab_selections ls ON ls.lab_order_id = lo.id
SQL
)

json_field() { python3 -c "import json,sys;print(json.load(open(sys.argv[1]))[sys.argv[2]])" "$1" "$2"; }

case "$COMMAND" in
  capture)
    echo "Capturing recovery sentinels from ${EXCHANGE_DB}..."
    OUT="$(psql_exchange -c "$CAPTURE_SQL")"
    if [ -z "$OUT" ] || [ "$OUT" = "" ]; then
      echo "REFUSED: no reviewed released order exists in ${EXCHANGE_DB}. Run the source golden journey first -- there is nothing to prove recovery of." >&2
      exit 1
    fi
    # Keycloak user ids for the synthetic staff accounts, so identity recovery is asserted by id and
    # not by "a user with that name can log in" (a reconciler can always recreate a name).
    KC_USERS="$(psql_keycloak -c "SELECT json_agg(json_build_object('username', username, 'id', id) ORDER BY username) FROM user_entity WHERE realm_id IN (SELECT id FROM realm WHERE name = 'dhealth-exchange')")"
    python3 - "$FILE" <<PY
import json, sys
sentinels = json.loads(r'''${OUT}''')
sentinels["keycloak_users"] = json.loads(r'''${KC_USERS}''')
with open(sys.argv[1], "w") as handle:
    json.dump(sentinels, handle, indent=2, sort_keys=True)
print("  order            " + sentinels["order_id"])
print("  ui reference     " + sentinels["order_reference_ui"] + "…")
print("  release version  " + sentinels["release_version_id"] + " (v" + str(sentinels["release_version_number"]) + ")")
print("  review           " + sentinels["review_id"] + " (" + sentinels["review_status"] + ")")
print("  patient          " + sentinels["patient_id"])
print("  fingerprint      " + sentinels["release_fingerprint"][:16] + "…  over " + str(sentinels["observation_count"]) + " observation(s)")
print("  keycloak users   " + ", ".join(u["username"] for u in sentinels["keycloak_users"] or []))
print("  counts           " + json.dumps(sentinels["counts"], sort_keys=True))
PY
    echo "Written to ${FILE}"
    ;;

  assert-absent)
    echo "Proving the target does NOT contain the source state..."
    FAILURES=0
    for pair in order_id:exchange_lab_orders review_id:exchange_doctor_reviews \
                release_version_id:exchange_release_versions patient_id:exchange_patients; do
      field="${pair%%:*}"; table="${pair##*:}"
      value="$(json_field "$FILE" "$field")"
      found="$(psql_exchange -c "SELECT count(*) FROM ${table} WHERE id = '${value}'")"
      if [ "$found" = "0" ]; then
        printf '  %-22s %s  ABSENT   OK\n' "$field" "${value:0:8}…"
      else
        printf '  %-22s %s  PRESENT  WRONG -- this is not a clean target\n' "$field" "${value:0:8}…"
        FAILURES=$((FAILURES + 1))
      fi
    done
    subs="$(psql_exchange -c "SELECT count(*) FROM exchange_identities WHERE subject IN (SELECT jsonb_array_elements_text('$(python3 -c "import json,sys;print(json.dumps(json.load(open(sys.argv[1]))['identity_subs']))" "$FILE")'::jsonb))")"
    if [ "$subs" = "0" ]; then
      printf '  %-22s %s  ABSENT   OK\n' "identity subjects" "(all)"
    else
      printf '  %-22s %s  PRESENT  WRONG -- the clean target already knows these subjects\n' "identity subjects" "$subs"
      FAILURES=$((FAILURES + 1))
    fi

    # The check that actually distinguishes a restored Keycloak database from a reconciler having run
    # again. The clean target's `keycloak-demo-users` one-shot creates the same three USERNAMES with
    # brand-new ids; only a restore brings the original ids back. Without this, identity recovery
    # would be asserted against rows that trivially agree with themselves.
    kc_ids="$(python3 -c "import json,sys;print(','.join(\"'\" + u['id'] + \"'\" for u in json.load(open(sys.argv[1]))['keycloak_users']))" "$FILE")"
    kc_found="$(psql_keycloak -c "SELECT count(*) FROM user_entity WHERE id IN (${kc_ids})")"
    if [ "$kc_found" = "0" ]; then
      printf '  %-22s %s  ABSENT   OK\n' "keycloak user ids" "(all)"
    else
      printf '  %-22s %s  PRESENT  WRONG -- the clean target already holds the source Keycloak users\n' "keycloak user ids" "$kc_found"
      FAILURES=$((FAILURES + 1))
    fi
    [ "$FAILURES" -eq 0 ] || { echo "REFUSED: the target is not clean; a restore into it would prove nothing." >&2; exit 1; }
    echo "Clean target confirmed: none of the source identifiers exist here."
    ;;

  assert-present)
    echo "Proving the SAME source state came back..."
    FAILURES=0
    check() {
      # check <label> <expected> <sql>
      actual="$("${NATIVE_COMPOSE[@]}" exec -T dhealth-db psql -X -At -U postgres -d "$EXCHANGE_DB" -c "$3" | tr -d '\r')"
      if [ "$actual" = "$2" ]; then
        printf '  %-34s %s\n' "$1" "OK"
      else
        printf '  %-34s WRONG (expected %s, got %s)\n' "$1" "$2" "$actual"
        FAILURES=$((FAILURES + 1))
      fi
    }
    ORDER_ID="$(json_field "$FILE" order_id)"
    REVIEW_ID="$(json_field "$FILE" review_id)"
    REVIEW_STATUS="$(json_field "$FILE" review_status)"
    VERSION_ID="$(json_field "$FILE" release_version_id)"
    VERSION_NUMBER="$(json_field "$FILE" release_version_number)"
    PATIENT_ID="$(json_field "$FILE" patient_id)"
    FINGERPRINT="$(json_field "$FILE" release_fingerprint)"
    OBS_COUNT="$(json_field "$FILE" observation_count)"

    check "the same order exists" 1 \
      "SELECT count(*) FROM exchange_lab_orders WHERE id = '${ORDER_ID}'"
    check "the same patient exists" 1 \
      "SELECT count(*) FROM exchange_patients WHERE id = '${PATIENT_ID}'"
    check "the same release version exists" 1 \
      "SELECT count(*) FROM exchange_release_versions WHERE id = '${VERSION_ID}' AND version_number = ${VERSION_NUMBER} AND lab_order_id = '${ORDER_ID}'"
    check "the same review exists, same status" 1 \
      "SELECT count(*) FROM exchange_doctor_reviews WHERE id = '${REVIEW_ID}' AND status = '${REVIEW_STATUS}' AND reviewed_at IS NOT NULL"
    check "the review is bound to that version" 1 \
      "SELECT count(*) FROM exchange_doctor_reviews WHERE id = '${REVIEW_ID}' AND release_version_id = '${VERSION_ID}' AND lab_order_id = '${ORDER_ID}'"
    check "released content is byte-identical" "$FINGERPRINT" \
      "SELECT encode(sha256(convert_to(string_agg(coalesce(o.source_code,'')||'|'||coalesce(o.numeric_value::text,'')||'|'||coalesce(o.numeric_unit,'')||'|'||coalesce(o.text_value,'')||'|'||coalesce(o.display_label_snapshot,''), E'\n' ORDER BY o.source_code, o.id), 'UTF8')),'hex') FROM exchange_release_manifest_entries me JOIN exchange_lab_observations o ON o.id = me.lab_observation_id WHERE me.release_version_id = '${VERSION_ID}'"
    check "the version binds the same observations" "$OBS_COUNT" \
      "SELECT count(*) FROM exchange_release_manifest_entries WHERE release_version_id = '${VERSION_ID}'"
    # Identity recovery, by subject value. The demo reconciler on a clean target mints NEW Keycloak
    # subjects, so these matching is evidence the identity state was restored.
    SUBS_JSON="$(python3 -c "import json,sys;print(json.dumps(json.load(open(sys.argv[1]))['identity_subs']))" "$FILE")"
    EXPECTED_SUBS="$(python3 -c "import json,sys;print(len(json.loads(sys.argv[1])))" "$SUBS_JSON")"
    check "the same Keycloak subjects are known" "$EXPECTED_SUBS" \
      "SELECT count(*) FROM exchange_identities WHERE subject IN (SELECT jsonb_array_elements_text('${SUBS_JSON}'::jsonb))"
    # Audit history, which is the point of having it after an incident.
    AUDIT_LATEST="$(json_field "$FILE" security_audit_latest_id)"
    check "the pre-backup audit event survives" 1 \
      "SELECT count(*) FROM exchange_security_audit_events WHERE id = '${AUDIT_LATEST}'"

    # Identity recovery proper: the SAME Keycloak user rows, by primary key. The demo reconciler runs
    # again when the restored stack comes up, but it converges an existing user by id (verified in
    # `reconcile-demo-users.sh` -- it looks a user up, then `update users/$id`, and never deletes),
    # so a matching id here means the Keycloak database was restored and not merely re-reconciled.
    KC_IDS="$(python3 -c "import json,sys;print(','.join(\"'\" + u['id'] + \"'\" for u in json.load(open(sys.argv[1]))['keycloak_users']))" "$FILE")"
    KC_EXPECTED="$(python3 -c "import json,sys;print(len(json.load(open(sys.argv[1]))['keycloak_users']))" "$FILE")"
    KC_ACTUAL="$(psql_keycloak -c "SELECT count(*) FROM user_entity WHERE id IN (${KC_IDS})" | tr -d '\r')"
    if [ "$KC_ACTUAL" = "$KC_EXPECTED" ]; then
      printf '  %-34s %s\n' "the same Keycloak user rows returned" "OK (${KC_ACTUAL}/${KC_EXPECTED} by id)"
    else
      printf '  %-34s WRONG (expected %s Keycloak user ids, found %s)\n' \
        "the same Keycloak user rows returned" "$KC_EXPECTED" "$KC_ACTUAL"
      FAILURES=$((FAILURES + 1))
    fi

    [ "$FAILURES" -eq 0 ] || { echo "REFUSED: ${FAILURES} sentinel check(s) failed -- recovery did not return the same state." >&2; exit 1; }
    echo "All sentinels returned: the same persisted objects, not equivalent-looking new ones."
    ;;

  *)
    echo "unknown command '${COMMAND}'" >&2
    exit 1
    ;;
esac
