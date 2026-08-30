package org.openelisglobal.dataexchange.fhir;

import java.util.List;
import org.openelisglobal.analysis.service.AnalysisService;
import org.openelisglobal.analysis.valueholder.Analysis;
import org.openelisglobal.common.exception.LIMSRuntimeException;
import org.openelisglobal.common.services.IStatusService;
import org.openelisglobal.common.services.StatusService.AnalysisStatus;
import org.openelisglobal.dataexchange.fhir.service.DaherAnalysisCorrelationService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronizationManager;

/**
 * DH-011J: the single outer transaction covering an added-test cancellation event's entire
 * native write -- resolve the target {@link Analysis} by its {@code daher_analysis_correlation}
 * row (the added-test namespace {@link DaherAddedTestTransaction} already populates; never the
 * OpenELIS-internal {@code fhirUuid} the pristine {@code deleteServiceRequest} keys on, which
 * DH-025 never learns and never stores), validate its current status against ADR-018's policy,
 * and perform the cancellation mutation -- all inside one transaction, so a failure between the
 * status check and the write can never leave a half-cancelled Analysis.
 *
 * <p>A separate {@code @Component} rather than {@code @Transactional} directly on
 * {@code ServiceRequestProvider}, for the same reason {@link DaherAddedTestTransaction} and
 * {@link DaherOrderCorrelationTransaction} document: a Spring transactional proxy around that
 * bean breaks HAPI's own reflection-based provider registration ({@code HAPI-0289}).
 *
 * <p><b>ADR-018 policy, mapped to the verified native status vocabulary (DH-011J, live-confirmed
 * against a disposable stack, see PROVENANCE.md):</b> "not yet accepted or started" is exactly
 * {@link AnalysisStatus#NotStarted} ("Not Tested", the confirmed default status of every newly
 * created Analysis, added-test or initial-order alike -- verified live, not assumed). Any other
 * status means the lab has already acted on this test -- ADR-018's "already in progress" branch
 * -- and this transaction refuses rather than silently overwriting technician/biologist/validated
 * work, exactly matching ADR-018's "manual handling" policy. This is a fail-closed allowlist (one
 * safe starting status), not a blocklist of specific "dangerous" statuses, so it does not need to
 * be revisited if a future OpenELIS upgrade adds a new status this class has never seen.
 *
 * <p><b>No hard deletes, no history loss:</b> the correlation row is never deleted or reused; the
 * Analysis row is never deleted; cancellation is exactly the same status-only mutation the native
 * {@code ServiceRequestProvider.deleteServiceRequest} already performs
 * ({@code analysis.setStatusId(Canceled)} + {@code analysisService.update()}), reused here rather
 * than reinvented, just reached through the correlation the adapter actually has instead of a
 * target-generated id it does not.
 */
@Component
public class DaherCancellationTransaction {

    @Autowired
    private DaherAnalysisCorrelationService daherAnalysisCorrelationService;

    @Autowired
    private AnalysisService analysisService;

    @Autowired
    private IStatusService statusService;

    public enum Outcome {
        /** Fresh mutation performed this call: was {@code NotStarted}, now {@code Canceled}. */
        CANCELLED,
        /** Idempotent no-op: already {@code Canceled} when this call resolved the target. */
        ALREADY_CANCELLED,
        /**
         * Found, but not in the one status ADR-018 allows an automatic cancellation from --
         * ADR-018's "already in progress" branch. Refused, not mutated. The caller (or a human)
         * must handle this manually, per ADR-018.
         */
        REFUSED_IN_PROGRESS,
        /** No {@code daher_analysis_correlation} row for this correlation value. */
        NOT_FOUND
    }

    public static final class Result {
        private final Outcome outcome;
        private final Long analysisId;
        private final AnalysisStatus status;

        private Result(Outcome outcome, Long analysisId, AnalysisStatus status) {
            this.outcome = outcome;
            this.analysisId = analysisId;
            this.status = status;
        }

        public Outcome getOutcome() {
            return outcome;
        }

        /** Null only for {@link Outcome#NOT_FOUND}. */
        public Long getAnalysisId() {
            return analysisId;
        }

        /** The Analysis's authoritative post-call status. Null only for {@link Outcome#NOT_FOUND}. */
        public AnalysisStatus getStatus() {
            return status;
        }
    }

    /**
     * @param correlationValue the added-test event's own {@code daher_analysis_correlation}
     *                         value -- the same identity {@link DaherAddedTestTransaction}
     *                         recorded when the test was created, never a re-derived or
     *                         target-generated id.
     * @param sysUserId        the acting user, recorded on the Analysis exactly as the native
     *                         delete path already does.
     */
    @Transactional(rollbackFor = Exception.class)
    public Result cancelByCorrelation(String correlationValue, String sysUserId) {
        List<DaherAnalysisCorrelation> matches = daherAnalysisCorrelationService
                .getByCorrelationValue(correlationValue);
        if (matches.isEmpty()) {
            return new Result(Outcome.NOT_FOUND, null, null);
        }
        if (matches.size() > 1) {
            // UNIQUE(correlation_value) should make this unreachable in committed state -- fail
            // loud rather than silently pick one, exactly as DaherAddedTestTransaction.addTest
            // and searchDaherCorrelationNatively already do for their own namespaces.
            throw new LIMSRuntimeException(
                    "Data integrity violation: multiple daher_analysis_correlation rows for " + correlationValue);
        }

        if (!TransactionSynchronizationManager.isActualTransactionActive()) {
            throw new IllegalStateException(
                    "DaherCancellationTransaction.cancelByCorrelation invoked outside an active transaction");
        }

        Long analysisId = matches.get(0).getAnalysisId();
        Analysis analysis = analysisService.get(String.valueOf(analysisId));
        if (analysis == null) {
            // No DELETE ever runs against Analysis (see class javadoc) -- a correlation row
            // outliving its Analysis would mean something else violated that invariant.
            throw new LIMSRuntimeException(
                    "daher_analysis_correlation " + correlationValue + " references missing Analysis " + analysisId);
        }

        AnalysisStatus currentStatus = statusService.getAnalysisStatusForID(analysis.getStatusId());

        if (currentStatus == AnalysisStatus.Canceled) {
            return new Result(Outcome.ALREADY_CANCELLED, analysisId, AnalysisStatus.Canceled);
        }
        if (currentStatus != AnalysisStatus.NotStarted) {
            return new Result(Outcome.REFUSED_IN_PROGRESS, analysisId, currentStatus);
        }

        analysis.setSysUserId(sysUserId);
        analysis.setStatusId(statusService.getStatusID(AnalysisStatus.Canceled));
        analysisService.update(analysis);

        return new Result(Outcome.CANCELLED, analysisId, AnalysisStatus.Canceled);
    }
}
