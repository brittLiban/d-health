# DAHER PATCH (UI-008/DH-008C): sourced from startup.sh, right after it sources
# startup-init.sh (which unconditionally repopulates $OMRS_MODULES_DIR from the
# read-only distribution/ directory on every boot -- confirmed directly from
# startup-init.sh's source).
#
# oauth2login's own OAuth2UserInfoAuthenticationScheme and the bundled
# "authentication" module's own DelegatingAuthenticationScheme are both
# @Component-annotated org.openmrs.api.context.AuthenticationScheme beans.
# OpenMRS's Context.setAuthenticationScheme() does a by-type Spring lookup that
# cannot disambiguate two beans of the same interface, and silently falls back
# to the platform default when it can't -- see PROJECT_STATE.md's UI-007/UI-008
# entries for the full measured chain. No global property resolves this: the
# module's own "authentication.scheme" property only configures what its
# DelegatingAuthenticationScheme delegates to *after* it has already won the
# ambiguous lookup.
#
# Removing this one file when SSO is enabled is config-only, not a database or
# SQL change: nothing here touches global_property, and the file reappears the
# moment SSO is turned back off, because startup-init.sh's own recopy (which
# runs before this script, every boot) always restores it from the image.
if [ "${SSO_ENABLED:-false}" = "true" ]; then
    rm -f "$OMRS_MODULES_DIR/authentication-2.3.0.omod"
fi
