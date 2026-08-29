FROM maven:3-eclipse-temurin-21@sha256:8f6ac126f7810bb5549c4cd122d2bf0e9cda5bdeb0838aa928f09e779fd8bef8 AS openelis-order-correlation-build

# DH-011D (BLOCKER-6/ADR-026, Option F): the pinned OpenELIS-Global-2 webapp image
# (itechuw/openelis-global-2:3.2.1.11, see docker-compose.yml) ships unpatched. This
# stage builds the exact same pinned tag from source and applies narrow, reviewed
# patches on top -- see patches/order-correlation/PROVENANCE.md for the full account of
# what each one changes and why, and for what remains deliberately NOT live-provable
# until the real numeric OpenELIS site code is supplied (NEEDS LIBAN #7): native
# ServiceRequest creation itself.
#
# This mirrors openmrs-config/Dockerfile's oauth2login-build/daherendadmission-build
# pattern: clone the pinned tag, assert the exact commit, apply patches from files
# already reviewed and committed in this repo, then run upstream's own unmodified
# build recipe. Reproducibility is verified at the source/patch/toolchain level, not
# by asserting a fixed artifact hash.
RUN --mount=target=/var/lib/apt/lists,type=cache,sharing=locked \
    --mount=target=/var/cache/apt,type=cache,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=60 -y update \
    && apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=60 \
        -y --no-install-recommends install git apache2-utils

COPY patches/order-correlation/ServiceRequestProvider.java.patch /patch/ServiceRequestProvider.java.patch
COPY patches/order-correlation/FhirTransformServiceImpl.java.patch /patch/FhirTransformServiceImpl.java.patch
COPY patches/order-correlation/LiquibaseConfig.java.patch /patch/LiquibaseConfig.java.patch
COPY patches/order-correlation/base-changelog.xml.patch /patch/base-changelog.xml.patch
COPY patches/order-correlation/ElectronicOrder.hbm.xml.patch /patch/ElectronicOrder.hbm.xml.patch
COPY patches/order-correlation/DaherOrderCorrelation.java /patch/DaherOrderCorrelation.java
COPY patches/order-correlation/DaherOrderCorrelationTransaction.java /patch/DaherOrderCorrelationTransaction.java
COPY patches/order-correlation/001-electronic-order-daher-correlation-unique.xml \
     /patch/001-electronic-order-daher-correlation-unique.xml
COPY patches/order-correlation/002-site-code-configuration.xml \
     /patch/002-site-code-configuration.xml
COPY patches/order-correlation/003-electronic-order-external-id-widen.xml \
     /patch/003-electronic-order-external-id-widen.xml

RUN git clone --branch 3.2.1.11 --depth 1 \
      https://github.com/DIGI-UW/OpenELIS-Global-2.git /build \
    && test "$(git -C /build rev-parse HEAD)" = e2a6bdf7999fa3754e7cdafbd2a91259c3f0ce23 \
    && git -C /build submodule update --init --depth 1 dataexport \
    && git -C /build apply /patch/ServiceRequestProvider.java.patch \
    && git -C /build apply /patch/FhirTransformServiceImpl.java.patch \
    && git -C /build apply /patch/LiquibaseConfig.java.patch \
    && git -C /build apply /patch/base-changelog.xml.patch \
    && git -C /build apply /patch/ElectronicOrder.hbm.xml.patch \
    && mkdir -p /build/src/main/java/org/openelisglobal/dataexchange/fhir \
    && cp /patch/DaherOrderCorrelation.java \
        /build/src/main/java/org/openelisglobal/dataexchange/fhir/DaherOrderCorrelation.java \
    && cp /patch/DaherOrderCorrelationTransaction.java \
        /build/src/main/java/org/openelisglobal/dataexchange/fhir/DaherOrderCorrelationTransaction.java \
    && mkdir -p /build/src/main/resources/liquibase/daher \
    && cp /patch/001-electronic-order-daher-correlation-unique.xml \
        /build/src/main/resources/liquibase/daher/001-electronic-order-daher-correlation-unique.xml \
    && cp /patch/002-site-code-configuration.xml \
        /build/src/main/resources/liquibase/daher/002-site-code-configuration.xml \
    && cp /patch/003-electronic-order-external-id-widen.xml \
        /build/src/main/resources/liquibase/daher/003-electronic-order-external-id-widen.xml

ARG DEFAULT_PW="adminADMIN!"
WORKDIR /build
RUN ./install/createDefaultPassword.sh -c -p ${DEFAULT_PW}

# Upstream's own two-stage build (dataexport submodule, then the main WAR),
# unmodified -- see /build/Dockerfile at the pinned commit for the source of this
# recipe.
WORKDIR /build/dataexport/dataexport-core
RUN --mount=type=cache,target=/root/.m2,sharing=locked mvn dependency:go-offline
RUN --mount=type=cache,target=/root/.m2,sharing=locked mvn clean install -DskipTests
WORKDIR /build/dataexport/
RUN --mount=type=cache,target=/root/.m2,sharing=locked mvn dependency:go-offline
RUN --mount=type=cache,target=/root/.m2,sharing=locked \
    mvn clean install -DskipTests \
    && mkdir -p /build/dataexport-m2/org \
    && cp -r /root/.m2/repository/org/itech /build/dataexport-m2/org/

WORKDIR /build
RUN --mount=type=cache,target=/root/.m2,sharing=locked \
    [ -d /root/.m2/repository/org/itech ] || { mkdir -p /root/.m2/repository/org && cp -r /build/dataexport-m2/org/itech /root/.m2/repository/org/; } \
    && mvn dependency:go-offline
RUN --mount=type=cache,target=/root/.m2,sharing=locked \
    [ -d /root/.m2/repository/org/itech ] || { mkdir -p /root/.m2/repository/org && cp -r /build/dataexport-m2/org/itech /root/.m2/repository/org/; } \
    && mvn clean install -Dmaven.test.skip=true -DskipITs=true -Dspotless.check.skip=true \
    && test -s /build/target/OpenELIS-Global.war

# Run stage -- identical to upstream's own Dockerfile run stage at this pinned
# commit, just sourced from the patched build above instead of a prebuilt image.
FROM tomcat:10-jre21@sha256:d936144d55fab00e7d2b73a1fff338600e2fc6597af9472617ad12f31b6bcafe

COPY --from=openelis-order-correlation-build /build/install/createDefaultPassword.sh ./

RUN rm -rf /usr/local/tomcat/webapps/* \
    /usr/local/tomcat/conf/Catalina/localhost/manager.xml

COPY --from=openelis-order-correlation-build /build/install/tomcat-resources/ROOT.war /usr/local/tomcat/webapps/ROOT.war
COPY --from=openelis-order-correlation-build /build/target/OpenELIS-Global.war /usr/local/tomcat/webapps/OpenELIS-Global.war

COPY --from=openelis-order-correlation-build /build/install/tomcat-resources/catalina.properties /usr/local/tomcat/conf/catalina.properties
COPY --from=openelis-order-correlation-build /build/install/tomcat-resources/logging.properties /usr/local/tomcat/conf/logging.properties

RUN mkdir -p /usr/local/tomcat/lib/org/apache/catalina/util
COPY --from=openelis-order-correlation-build /build/install/tomcat-resources/ServerInfo.properties /usr/local/tomcat/lib/org/apache/catalina/util/ServerInfo.properties

RUN groupadd tomcat; \
    groupadd tomcat-ssl-cert -g 8443; \
    useradd -M -s /bin/bash -u 8443 tomcat_admin; \
    usermod -a -G tomcat,tomcat-ssl-cert tomcat_admin; \
    chown -R tomcat_admin:tomcat $CATALINA_HOME; \
    chmod g-w,o-rwx $CATALINA_HOME; \
    chmod g-w,o-rwx $CATALINA_HOME/conf; \
    chmod o-rwx $CATALINA_HOME/logs; \
    chmod o-rwx $CATALINA_HOME/temp; \
    chmod g-w,o-rwx $CATALINA_HOME/bin; \
    chmod g-w,o-rwx $CATALINA_HOME/webapps; \
    chmod 770 $CATALINA_HOME/conf/catalina.policy; \
    chmod g-w,o-rwx $CATALINA_HOME/conf/catalina.properties; \
    chmod g-w,o-rwx $CATALINA_HOME/conf/context.xml; \
    chmod g-w,o-rwx $CATALINA_HOME/conf/logging.properties; \
    chmod g-w,o-rwx $CATALINA_HOME/conf/server.xml; \
    chmod g-w,o-rwx $CATALINA_HOME/conf/tomcat-users.xml; \
    chmod g-w,o-rwx $CATALINA_HOME/conf/web.xml; \
    mkdir -p /var/lib/openelis-global/logs/; \
    chown -R tomcat_admin:tomcat /var/lib/openelis-global/logs/; \
    mkdir -p /var/lib/openelis-global/properties/; \
    chown -R tomcat_admin:tomcat /var/lib/openelis-global/properties/; \
    mkdir -p /var/lib/openelis-global/configuration/; \
    chown -R tomcat_admin:tomcat /var/lib/openelis-global/configuration/;

COPY --from=openelis-order-correlation-build /build/install/openelis_healthcheck.sh /healthcheck.sh
RUN chown tomcat_admin:tomcat /healthcheck.sh; chmod 770 /healthcheck.sh;

COPY --from=openelis-order-correlation-build /build/install/docker-entrypoint.sh /docker-entrypoint.sh
RUN chown tomcat_admin:tomcat /docker-entrypoint.sh; chmod 770 /docker-entrypoint.sh;

COPY --from=openelis-order-correlation-build /build/tomcat/oe_server.xml /usr/local/tomcat/conf/server.xml
USER root

ENTRYPOINT [ "/docker-entrypoint.sh" ]
