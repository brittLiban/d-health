#!/bin/sh
set -eu

patient_control=/openmrs/distribution/openmrs_config/globalproperties/zz-daher-patient-control/patients.xml

if [ ! -r "$patient_control" ]; then
    echo "Demo-patient control file is missing or unreadable: $patient_control" >&2
    exit 1
fi

if grep -Fq '<value>50</value>' "$patient_control"; then
    echo "OMRS_DEMO_PATIENTS=on requires OMRS_DEMO_CONTENT=on (the demo generator needs the upstream OpenMRS ID metadata)" >&2
    exit 1
fi

exec /openmrs/configure-sso.sh
