package org.openelisglobal.dataexchange.fhir;

import jakarta.servlet.http.HttpServletRequest;
import java.sql.Timestamp;
import java.util.List;
import org.openelisglobal.common.exception.LIMSRuntimeException;
import org.openelisglobal.common.log.LogEvent;
import org.openelisglobal.common.util.DateUtil;
import org.openelisglobal.dataexchange.fhir.service.DaherAnalysisCorrelationService;
import org.openelisglobal.sample.form.SampleEditForm;
import org.openelisglobal.sample.service.SampleEditService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronizationManager;

/**
 * DH-011P (Design B, LIBAN DECISION: SELECT DESIGN B): the single outer transaction covering an
 * "add a test to an existing accession" DH-025 source event's entire native write --
 * {@link SampleEditService#editSample} (which creates exactly one new {@code Analysis}, or
 * revives exactly one cancelled one -- see {@code newOrExistingCanceledAnalysis}) and the
 * {@link DaherAnalysisCorrelation} row for the event -- so a failure persisting the correlation,
 * or a race the {@code UNIQUE(correlation_value)} constraint rejects, rolls the native Analysis
 * write back too instead of leaving an orphaned test with no correlation.
 *
 * <p>A separate {@code @Component} rather than {@code @Transactional} directly on
 * {@code ServiceRequestProvider}, for the exact reason {@link DaherOrderCorrelationTransaction}
 * documents: {@code ServiceRequestProvider} is reflected on by HAPI's {@code FhirRestfulServer}
 * to find its {@code @Create}/{@code @Update}/{@code @Search}/{@code @Delete} methods, and a
 * Spring transactional proxy around that bean breaks that reflection ({@code HAPI-0289}, live
 * -confirmed during DH-011D3).
 *
 * <p><b>Idempotency:</b> {@link #addTest} performs a fast, non-authoritative pre-check against
 * {@code daher_analysis_correlation} before calling {@code editSample} -- purely so a replayed
 * event does not insert a second, spurious {@code Analysis} on the common non-racing case. The
 * authoritative guarantee against a true concurrent duplicate is the DB-level
 * {@code UNIQUE(correlation_value)} constraint: if two concurrent deliveries of the same event
 * both pass the pre-check, both call {@code editSample} (each creating its own {@code Analysis}),
 * and both attempt to insert the same {@code correlation_value} -- exactly one insert succeeds,
 * the other raises a constraint-violation exception that rolls its entire transaction back,
 * undoing its own spurious {@code Analysis} insert with it. The loser's HTTP caller (the Daher
 * adapter's {@code deliver_service_request}) never trusts a POST's status alone regardless -- it
 * always re-probes by correlation afterward, per DH-011D3 finding C.
 */
@Component
public class DaherAddedTestTransaction {

    @Autowired
    private SampleEditService sampleEditService;

    @Autowired
    private DaherAnalysisCorrelationService daherAnalysisCorrelationService;

    /**
     * DH-011P: distinguishes a freshly-created/revived Analysis from an idempotent replay that
     * wrote nothing, so the FHIR caller can report the correct {@code MethodOutcome.setCreated}
     * instead of always claiming 201 -- a replay that silently reports "created" is the same
     * class of false-green as the Patient-create bug PROVENANCE.md already records, and must not
     * be left equally undocumented here.
     */
    public static final class Result {
        private final String analysisId;
        private final boolean created;

        private Result(String analysisId, boolean created) {
            this.analysisId = analysisId;
            this.created = created;
        }

        public String getAnalysisId() {
            return analysisId;
        }

        public boolean isCreated() {
            return created;
        }
    }

    /**
     * @return the id of the {@code Analysis} the correlation now (or already) targets, and
     *         whether this call actually wrote it: either freshly created/revived
     *         ({@code isCreated() == true}), or found by the pre-check to already exist for this
     *         exact correlation value ({@code isCreated() == false} -- an idempotent replay, no
     *         new write performed).
     */
    @Transactional(rollbackFor = Exception.class)
    public Result addTest(String correlationValue, SampleEditForm form, HttpServletRequest request,
            String sysUserId) {

        List<DaherAnalysisCorrelation> existing = daherAnalysisCorrelationService
                .getByCorrelationValue(correlationValue);
        if (existing.size() == 1) {
            return new Result(String.valueOf(existing.get(0).getAnalysisId()), false);
        }
        if (existing.size() > 1) {
            // The UNIQUE(correlation_value) constraint should make this unreachable in
            // committed state -- fail loud rather than silently pick one, exactly as
            // searchDaherCorrelationNatively already does for the electronic_order namespace.
            throw new LIMSRuntimeException(
                    "Data integrity violation: multiple daher_analysis_correlation rows for " + correlationValue);
        }

        if (!TransactionSynchronizationManager.isActualTransactionActive()) {
            // DH-011P Section 8 evidence requirement: the correlation insert below must share
            // one transaction with editSample's Analysis write, not merely run after it.
            throw new IllegalStateException(
                    "DaherAddedTestTransaction.addTest invoked outside an active transaction");
        }

        List<String> updatedAnalysisIds = sampleEditService.editSample(form, request, null, false, sysUserId);

        if (updatedAnalysisIds.size() != 1) {
            // form.getPossibleTests() carries exactly one add=true item (see
            // ServiceRequestProvider.createServiceRequest's existing-specimen branch) and no
            // existingTests, so editSample's own add-analysis loop must produce exactly one id.
            // A different count means either the form was built wrong or editSample's own
            // semantics changed under us -- either way, do not guess which id is "the" one.
            LogEvent.logError(DaherAddedTestTransaction.class.getSimpleName(), "addTest",
                    "editSample returned " + updatedAnalysisIds.size() + " analysis ids for a single add-test event ("
                            + correlationValue + "); expected exactly 1");
            throw new LIMSRuntimeException(
                    "Unexpected analysis count (" + updatedAnalysisIds.size() + ") for add-test event");
        }

        String analysisId = updatedAnalysisIds.get(0);

        DaherAnalysisCorrelation correlation = new DaherAnalysisCorrelation();
        correlation.setAnalysisId(Long.valueOf(analysisId));
        correlation.setCorrelationValue(correlationValue);
        Timestamp now = DateUtil.getNowAsTimestamp();
        correlation.setCreatedAt(now);
        daherAnalysisCorrelationService.insert(correlation);

        return new Result(analysisId, true);
    }
}
