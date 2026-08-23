# DAHER PATCH (UI-008/DH-008C): sourced from startup.sh in the same post-boot
# window the vendor's own script already uses to trigger first-boot filter
# initialization (right after its "Trigger first filter to start data import"
# sleep/curl pair, before it waits on the foreground Tomcat process).
#
# Solves the oauth2login.started=false persistence gotcha (UI-006/UI-007)
# without SQL and without direct database access.
#
# UI-008 measured, in this order, before landing on this design:
#   1. Calling the supported ModuleActionResource1_8 REST operation
#      (POST /ws/rest/v1/moduleaction, action=START -- the same code path the
#      admin UI's module "Start" action uses) DOES reliably flip
#      oauth2login.started to true in the database, every time it was tried.
#   2. But it does NOT make /oauth2login reachable within the same container
#      lifecycle. Measured three times: the module's own triggered context
#      refresh ("Refreshing Context" / "Done refreshing Context" in the log)
#      completes in single-digit seconds, then /oauth2login keeps 404ing
#      indefinitely -- confirmed by waiting 5 minutes, then 20 minutes, past
#      that refresh's completion with no change. This is not a patience
#      problem. A natural boot that starts with oauth2login.started already
#      true, by contrast, reaches a reachable /oauth2login in under 30 seconds
#      every time this was measured. Whatever OpenMRS's REST-triggered,
#      already-initialised-context module start does internally, it does not
#      reach the same end state as a fresh Tomcat deployment starting the
#      module as part of its own boot.
#
# The fix that actually works with this constraint: don't try to hot-activate
# the module inside the running container. Instead, ensure the module is
# *eligible to auto-start on the next boot* and then fail loudly so the
# platform's own restart policy (docker-compose.yml's `restart:
# unless-stopped` on openmrs-backend) gives it that next boot.
# ModuleFactory.getModulesThatShouldStart() (openmrs-core 2.8.7) starts a
# module if global property "<id>.started" is null/true, OR global property
# "<id>.mandatory" is "true", OR Module.isMandatory() is true -- three
# independent conditions. Module.isMandatory() (set only from config.xml,
# unset in this repo's patch) is the one that arms
# ModuleFactory.stopModule()'s MandatoryModuleException guard (measured
# directly in this ticket: setting config.xml's <mandatory> element made
# OAuth2LoginActivator.willStart()'s own self-unload-when-SSO-off throw that
# exception at boot -- reverted, recorded in PROJECT_STATE.md). The
# "<id>.mandatory" *global property* is a separate, independent input to the
# same OR and does not touch Module.isMandatory() or that guard at all -- so
# setting it via the standard SystemSettingResource1_9 REST resource
# (POST /ws/rest/v1/systemsetting, the same GlobalProperty write the admin
# settings UI performs) makes oauth2login always attempt to start on every
# future boot, while leaving OAuth2LoginActivator.willStart()'s own
# oauth2.enabled check free to immediately self-stop+unload it when SSO is
# off, exactly as before.
#
# Once set, this property is durable: the next boot's
# getModulesThatShouldStart() includes oauth2login unconditionally, so this
# script becomes a no-op after the one restart that follows the first time
# SSO is turned on against a database that has oauth2login.started=false.
#
# This file is *sourced*, not executed, into startup.sh's own shell (which
# runs "-aeu"). A bare `exit` here would terminate startup.sh itself -- which
# is exactly what we want in the one genuinely fatal case (see below) so the
# container actually restarts, but every non-fatal path still uses `return`.
#
# Two guards found necessary by testing against a genuinely fresh database
# (not just an already-initialized one with a stale started=false flag):
#
# 1. A brand-new install has no openmrs-runtime.properties file yet and
#    answers every request, including this script's own reachability probe,
#    while OpenMRS's own first-time setup wizard is still running in the
#    background -- there is no started=false to heal on a database that has
#    never started the module at all (getModulesThatShouldStart() starts it
#    by default when the global property doesn't exist yet), so attempting
#    the systemsetting REST call this early just fails (the REST API isn't
#    functional until setup completes) and produced a genuine crash loop
#    (RestartCount climbing past 20 within a minute) before this guard was
#    added. Gate on the runtime-properties file existing -- its absence is
#    the exact condition OpenMRS itself logs as "Initial setup is needed."
#
# 2. If oauth2login.mandatory is already "true" when this script runs, a
#    previous boot already did its job and something else is preventing
#    activation -- exiting 1 again would restart-loop forever rather than
#    the intended single corrective restart. Detect that and fail loud
#    without exiting, leaving a running-but-degraded container instead of an
#    unbounded loop.

daher_activate_oauth2login() {
    if [ "${SSO_ENABLED:-false}" != "true" ]; then
        return 0
    fi

    if [ ! -f /openmrs/data/openmrs-runtime.properties ]; then
        echo "OpenMRS has not completed its own first-time setup yet (no runtime properties file); nothing to activate this boot."
        return 0
    fi

    daher_oauth2login_status() {
        curl -s -m 10 -o /dev/null -w '%{http_code}' "http://localhost:8080/${OMRS_WEBAPP_NAME}/oauth2login" || true
    }

    # A wizard-incomplete OpenMRS answers EVERY path under /openmrs/, including
    # this one, with a plain 302 to /initialsetup -- measured directly this
    # ticket. The status code alone cannot tell that apart from oauth2login's
    # own genuine 302 to the identity provider, so this checks the redirect
    # target, not just the code: active only if it is a 302 that does NOT
    # point at the setup wizard.
    daher_oauth2login_active() {
        daher_location=$(curl -s -m 10 -o /dev/null -w '%{redirect_url}' "http://localhost:8080/${OMRS_WEBAPP_NAME}/oauth2login" 2>/dev/null || true)
        case "$daher_location" in
            *initialsetup*|"") return 1 ;;
            *) return 0 ;;
        esac
    }

    echo "Waiting for oauth2login's own dispatch to become reachable before checking activation..."
    daher_attempt=0
    while [ "$(daher_oauth2login_status)" = "000" ]; do
        daher_attempt=$((daher_attempt + 1))
        if [ "$daher_attempt" -ge 120 ]; then
            echo "FATAL: OpenMRS never became reachable to check oauth2login's activation state" >&2
            exit 1
        fi
        sleep 5
    done

    if daher_oauth2login_active; then
        echo "oauth2login is already active; no activation needed."
        return 0
    fi

    daher_current_mandatory=$(curl -s -u "admin:${OMRS_ADMIN_USER_PASSWORD}" \
        "http://localhost:8080/${OMRS_WEBAPP_NAME}/ws/rest/v1/systemsetting/oauth2login.mandatory" 2>/dev/null) || true
    if echo "$daher_current_mandatory" | grep -q '"value":"true"'; then
        echo "FATAL: oauth2login.mandatory is already true from a previous boot and oauth2login is still not active -- restarting again would loop without fixing anything. Leaving this container running in a degraded state (SSO enabled, oauth2login not reachable) rather than crash-looping. This needs investigation, not another restart." >&2
        return 0
    fi

    echo "oauth2login did not self-activate (see PROJECT_STATE.md's UI-006/UI-008 oauth2login.started=false finding). Marking it always-eligible-to-start via the supported systemsetting REST resource, then failing loudly so the platform's restart policy gives it a fresh boot -- in-place REST module-start was measured this ticket to update the database but never restore actual HTTP dispatch within the same container lifecycle."
    daher_mandatory_response=$(curl -s -u "admin:${OMRS_ADMIN_USER_PASSWORD}" \
        -H "Content-Type: application/json" \
        -X POST \
        -d '{"property":"oauth2login.mandatory","value":"true"}' \
        "http://localhost:8080/${OMRS_WEBAPP_NAME}/ws/rest/v1/systemsetting") || true

    if ! echo "$daher_mandatory_response" | grep -q '"value":"true"'; then
        echo "FATAL: could not set oauth2login.mandatory=true via the supported systemsetting REST operation. Response was: ${daher_mandatory_response}" >&2
        exit 1
    fi

    echo "FATAL: oauth2login.started was false this boot; it is now marked mandatory for the next boot and this container must restart for it to take effect. Exiting non-zero so the platform's restart policy (openmrs-backend: restart: unless-stopped) provides that boot." >&2
    exit 1
}

daher_activate_oauth2login
