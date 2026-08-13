FROM openmrs/openmrs-reference-application-3-backend:3.7.x@sha256:440bf92f4749faa38ca48a3e09f6651dee016089f0cbf6031dc5b1a979000ed7 AS base

# Upstream's referenceapplication-demo global-properties file also sets the
# demo-patient trigger to 50. Our runtime-mounted patients.on/off.xml must be
# the only writer for that property: Initializer does not promise that sibling
# directories are processed lexically, so a later upstream write could turn an
# explicit `off` back into 50 patients. Remove only that four-line property
# block from the exact pinned source and retain its six unrelated properties.
USER root
RUN upstream_demo_file=/openmrs/distribution/openmrs_config/globalproperties/referenceapplication-demo/globalproperties-core_demo.xml \
    && sed -i '/<globalProperty>/{N;N;N;/referencedemodata.createDemoPatientsOnNextStartup/d;}' \
       "$upstream_demo_file" \
    && ! grep -q referencedemodata.createDemoPatientsOnNextStartup "$upstream_demo_file" \
    && test "$(grep -c globalProperty "$upstream_demo_file")" = 12
USER 1001

# Preserve upstream's demo concepts and metadata after separating the patient
# trigger above. This is the default target.
FROM base AS on

# Initializer 2.12's exclusion API is not honored by every loader. Removing the
# explicitly named demo overlay at build time makes `off` truthful without
# changing database rows or running the OpenMRS container as root.
FROM base AS off
USER root
RUN find /openmrs/distribution/openmrs_config -type d -name referenceapplication-demo -prune \
      -exec rm -rf -- {} +
COPY --chmod=0755 validate-demo-options.sh /openmrs/validate-demo-options.sh
USER 1001
CMD ["/openmrs/validate-demo-options.sh"]
