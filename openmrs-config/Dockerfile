FROM maven:3.9.11-eclipse-temurin-17@sha256:e4a7ace3dc0d645ed97f8d9ad0b0d3f0b14fa8d150138f27f116d7105a639b82 AS oauth2login-build

# OpenMRS's official OAuth2 Login module is not included in the O3 3.7.x
# backend. Build its released 1.5.0 tag from the exact commit rather than
# downloading an unversioned OMOD or relying on a moving Maven repository.
#
# UI-008/DH-008C applies the Daher bridge patch (proven end-to-end in UI-007)
# on top of that pinned source before building, rather than downloading an
# opaque prebuilt patched .omod: the new filter class and the config.xml
# addition (the /oauth2login bridge -- see
# patches/oauth2login-bridge/PROVENANCE.md) are applied here, from the files
# already reviewed and committed in this repo. The Maven build itself is not
# byte-for-byte reproducible (jar entry timestamps), so reproducibility is
# verified at the source/patch/toolchain level -- this exact clone command,
# this exact patch, this exact base image digest -- not by asserting a fixed
# artifact hash.
#
# ADR-017E2/DH-008G additionally applies OAuth2LoginController.java.patch,
# which replaces upstream's own provider-claim check (a genuinely absent
# claim silently defaults to Provider-activated -- fail-open) with a
# role-derived, fail-closed decision reusing the same "roles" claim OpenMRS
# role assignment already consumes: exact "Provider" client-role membership,
# nothing else. See that patch file's own header comment and
# PROVENANCE.md for the full account.
#
# Bug #25 additionally applies OAuth2UserInfoAuthenticationScheme.java.patch.
# OpenMRS creates first-login users in a daemon thread/session and returns the
# committed User, while oauth2login 1.5.0 discarded that return value and
# reread through its already-open REPEATABLE_READ transaction. The reread
# cannot see the new row, so the first response is unauthenticated. The patch
# uses OpenMRS's authoritative returned User directly; it adds no retry, sleep,
# cache flush, or new transaction boundary.
COPY patches/oauth2login-bridge/OAuth2LoginBridgeFilter.java /patch/OAuth2LoginBridgeFilter.java
COPY patches/oauth2login-bridge/config.xml.patch /patch/config.xml.patch
COPY patches/oauth2login-bridge/OAuth2LoginController.java.patch /patch/OAuth2LoginController.java.patch
COPY patches/oauth2login-bridge/OAuth2UserInfoAuthenticationScheme.java.patch /patch/OAuth2UserInfoAuthenticationScheme.java.patch
RUN git clone --branch 1.5.0 --depth 1 \
      https://github.com/openmrs/openmrs-module-oauth2login.git /src \
    && test "$(git -C /src rev-parse HEAD)" = 2770233627343a53719ec981e997d94b1d90b7ad \
    && cp /patch/OAuth2LoginBridgeFilter.java \
        /src/omod/src/main/java/org/openmrs/module/oauth2login/web/filter/OAuth2LoginBridgeFilter.java \
    && git -C /src apply /patch/config.xml.patch \
    && git -C /src apply /patch/OAuth2LoginController.java.patch \
    && git -C /src apply /patch/OAuth2UserInfoAuthenticationScheme.java.patch \
    && mvn -B -f /src/pom.xml -Dmaven.test.skip=true package \
    && test -s /src/omod/target/oauth2login-1.5.0.omod

FROM maven:3.9-eclipse-temurin-8@sha256:15522857a08bc468b05f4284d4a5a6c49eff7af7abfe16dc7770609484a67b8b AS daherendadmission-build

# ADR-030/UI-015E ("Terminer l'admission" -- see DECISIONS.md ADR-030): builds the module from the
# source already committed in this repo (openmrs-config/modules/daherendadmission/), the same
# reproducible-from-source approach oauth2login-build above uses for its pinned upstream clone --
# not an opaque prebuilt .omod. JDK 8, matching the toolchain this module was actually built and
# live-proven against (openmrs.platform.version=2.8.7).
COPY modules/daherendadmission /src
RUN mvn -q -B -f /src/pom.xml -Dmaven.test.skip=true package \
    && test -s /src/omod/target/daherendadmission-1.0.0.omod

FROM openmrs/openmrs-reference-application-3-backend:3.7.x@sha256:440bf92f4749faa38ca48a3e09f6651dee016089f0cbf6031dc5b1a979000ed7 AS base

COPY --from=oauth2login-build --chown=1001:1001 \
  /src/omod/target/oauth2login-1.5.0.omod \
  /openmrs/distribution/openmrs_modules/oauth2login-1.5.0.omod
COPY --from=daherendadmission-build --chown=1001:1001 \
  /src/omod/target/daherendadmission-1.0.0.omod \
  /openmrs/distribution/openmrs_modules/daherendadmission-1.0.0.omod
COPY --chmod=0755 configure-sso.sh /openmrs/configure-sso.sh
COPY --chmod=0644 exclude-authentication-when-sso.sh /openmrs/exclude-authentication-when-sso.sh
COPY --chmod=0644 activate-oauth2login-if-needed.sh /openmrs/activate-oauth2login-if-needed.sh

# UI-008/DH-008C: oauth2login's own AuthenticationScheme bean and the bundled
# "authentication" module's DelegatingAuthenticationScheme bean collide in
# OpenMRS's by-type Spring lookup (see PROJECT_STATE.md's UI-007/UI-008
# entries for the full measured chain; no config.xml/global-property fixes
# that ambiguity directly). Excluding the module's omod when SSO is enabled
# is config-only: startup-init.sh unconditionally repopulates
# $OMRS_MODULES_DIR from the read-only distribution/ directory on every boot,
# before this line's inserted call runs, so the exclusion is re-evaluated
# fresh every boot rather than being a one-time deletion, and the module
# reappears the instant SSO is turned back off. This is the one stable anchor
# line in the vendor's own startup.sh (immediately after it sources
# startup-init.sh, which is what performs that repopulation).
#
# The second insertion solves the oauth2login.started=false persistence
# gotcha (UI-006/UI-007): a database that had SSO disabled once, and is later
# turned on, does not restart the module by itself (confirmed directly from
# ModuleFactory.getModulesThatShouldStart()'s source). Anchored on the
# vendor's own "Trigger first filter to start data import" comment, the one
# point in startup.sh after Tomcat is up but before it waits on the
# foreground process -- see activate-oauth2login-if-needed.sh for exactly why
# this does NOT try to hot-start the module in place (measured this ticket to
# update the database but never restore live HTTP dispatch within the same
# container lifecycle) and instead marks it eligible to start on the next
# boot via the supported systemsetting REST resource, then exits non-zero so
# openmrs-backend's own `restart: unless-stopped` policy provides that boot.
USER root
RUN startup_script=/openmrs/startup.sh \
    && sed -i '/^source \/openmrs\/startup-init\.sh$/a source /openmrs/exclude-authentication-when-sso.sh' \
       "$startup_script" \
    && grep -q '^source /openmrs/exclude-authentication-when-sso\.sh$' "$startup_script" \
    && test "$(grep -c '^source /openmrs/exclude-authentication-when-sso\.sh$' "$startup_script")" = 1 \
    && sed -i '/^# Trigger first filter to start data import$/a source /openmrs/activate-oauth2login-if-needed.sh' \
       "$startup_script" \
    && grep -q '^source /openmrs/activate-oauth2login-if-needed\.sh$' "$startup_script" \
    && test "$(grep -c '^source /openmrs/activate-oauth2login-if-needed\.sh$' "$startup_script")" = 1
USER 1001

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

# UI-015E/ADR-030: OWASP CSRFGuard (bundled with OpenMRS core, WEB-INF/csrfguard.properties inside
# openmrs.war) rejects any POST not in its unprotected allowlist by default -- only
# %servletContext%/ws/*, /initialsetup, and /csrfguard are covered out of the box, not arbitrary
# module servlet paths. Live-tested before writing this: sed-patching the exploded
# webapps/openmrs/WEB-INF/csrfguard.properties directly on a running container does NOT survive a
# restart, because Tomcat re-explodes that directory from openmrs.war whenever their mtimes
# differ, discarding the live edit -- the WAR itself has to carry the patch. This uses the JDK's
# own `jar` tool (already on PATH in this base image) to extract, append one line to, and
# re-insert the single properties file -- no new third-party dependency, and it removes CSRFGuard
# protection only for this one narrow endpoint prefix, not app-wide (the endpoint's own
# authorization -- see DaherMaternityAdmissionServiceImpl -- and the existing X-Daher-App-Request
# convention are what still gate it).
USER root
RUN war=/openmrs/distribution/openmrs_core/openmrs.war \
    && workdir=$(mktemp -d) \
    && cd "$workdir" \
    && jar xf "$war" WEB-INF/csrfguard.properties \
    && ! grep -q '^org.owasp.csrfguard.unprotected.DaherEndAdmission' WEB-INF/csrfguard.properties \
    && printf '%s\n' 'org.owasp.csrfguard.unprotected.DaherEndAdmission = %servletContext%/moduleServlet/daherendadmission/*' \
       >> WEB-INF/csrfguard.properties \
    && jar uf "$war" WEB-INF/csrfguard.properties \
    && cd / && rm -rf "$workdir"
USER 1001

CMD ["/openmrs/configure-sso.sh"]

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
