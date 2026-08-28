package org.openelisglobal.dataexchange.fhir;

import java.util.regex.Pattern;

/**
 * DH-011D (Daher Health / DH-025): constants shared between {@code ServiceRequestProvider}
 * (which persists the incoming correlation on create) and {@code FhirTransformServiceImpl}
 * (which must echo the same identifier back on every outbound-synced {@code ServiceRequest} so
 * the search half -- already patched separately -- can actually find it). Kept in one place
 * specifically so the two sides cannot drift into using different system URIs.
 *
 * <p>{@link #SYSTEM} must match {@code adapter/src/daher_adapter/recovery.py}'s
 * {@code SOURCE_CORRELATION_SYSTEM} exactly -- this is the OpenELIS-side half of an
 * already-decided, already-implemented adapter contract, not a locally invented convention.
 */
public final class DaherOrderCorrelation {

    public static final String SYSTEM = "urn:daher-health:adapter:source-event";

    // The adapter's correlation value is a SHA-256 hex digest
    // (hashlib.sha256(...).hexdigest()) -- exactly 64 lowercase hex characters.
    public static final Pattern VALUE_PATTERN = Pattern.compile("^[0-9a-f]{64}$");

    private DaherOrderCorrelation() {
    }
}
