#!/bin/sh
set -eu

patient_control=/demo-control/patients.xml

if [ ! -r "$patient_control" ]; then
    echo "Demo-patient control file is missing or unreadable: $patient_control" >&2
    exit 1
fi

# Patient generation is opt-in. Keep the default path independent of database
# schema state so an empty database can initialize normally.
if ! grep -Fq '<value>50</value>' "$patient_control"; then
    echo "[daher] Demo-patient preflight skipped (patients off)"
    exit 0
fi

: "${OMRS_DB_HOST:?validate-demo-readiness.sh: OMRS_DB_HOST not set}"
: "${OMRS_DB_NAME:?validate-demo-readiness.sh: OMRS_DB_NAME not set}"
: "${OMRS_DB_USER:?validate-demo-readiness.sh: OMRS_DB_USER not set}"
: "${OMRS_DB_PASSWORD:?validate-demo-readiness.sh: OMRS_DB_PASSWORD not set}"

MYSQL_PWD=$OMRS_DB_PASSWORD
export MYSQL_PWD

if ! identifier_types=$(mariadb --host="$OMRS_DB_HOST" --user="$OMRS_DB_USER" \
    --database="$OMRS_DB_NAME" --batch --skip-column-names \
    --execute="SELECT COUNT(*) FROM patient_identifier_type WHERE name = 'OpenMRS ID' AND retired = 0" \
    2>/dev/null); then
    echo "OMRS_DEMO_PATIENTS=on requires one completed OMRS_DEMO_CONTENT=on, OMRS_DEMO_PATIENTS=off boot first" >&2
    exit 1
fi

if [ "$identifier_types" != 1 ]; then
    echo "OMRS_DEMO_PATIENTS=on requires exactly one active OpenMRS ID identifier type; boot content on with patients off first" >&2
    exit 1
fi

echo "[daher] Demo-patient metadata preflight passed"
