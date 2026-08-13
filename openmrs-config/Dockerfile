FROM openmrs/openmrs-reference-application-3-backend:3.7.x@sha256:440bf92f4749faa38ca48a3e09f6651dee016089f0cbf6031dc5b1a979000ed7 AS base

# Preserve upstream's distribution exactly. This is the default target.
FROM base AS on

# Initializer 2.12's exclusion API is not honored by every loader. Removing the
# explicitly named demo overlay at build time makes `off` truthful without
# changing database rows or running the OpenMRS container as root.
FROM base AS off
USER root
RUN find /openmrs/distribution/openmrs_config -type d -name referenceapplication-demo -prune \
      -exec rm -rf -- {} +
USER 1001
