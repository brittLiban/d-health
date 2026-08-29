package org.openelisglobal.dataexchange.fhir;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.SequenceGenerator;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;
import java.sql.Timestamp;
import org.openelisglobal.common.valueholder.BaseObject;

/**
 * DH-011P (Design B, LIBAN DECISION: SELECT DESIGN B): a standalone, Daher-owned correlation
 * for a single "add a test to an existing accession" DH-025 source event -- deliberately not a
 * column on {@code Analysis} or a second value on {@code ElectronicOrder.externalId}, both of
 * which are scalar and can carry only one correlation for the object's entire lifetime. See
 * {@code PROVENANCE.md}'s DH-011O/DH-011P sections for why the existing mechanism cannot
 * represent independent, repeatable "add a test" events without breaking the original-order
 * correlation's own authoritative reconciliation.
 *
 * <p><b>{@code analysisId} is unique (DH-011P3).</b> One Analysis may carry at most one Daher
 * added-test correlation, enforced at the database
 * ({@code 005-daher-analysis-correlation-unique-analysis.xml}), not merely by application logic.
 * {@code SampleEditServiceImpl.newOrExistingCanceledAnalysis} can revive a cancelled
 * {@code Analysis} row on a matching re-add rather than inserting a new one -- migration 004
 * originally left this column non-unique specifically to tolerate that revival case, since it
 * would otherwise let one Analysis legitimately be the target of two add-events across its
 * lifecycle. Liban reviewed that tradeoff and chose the strict invariant instead: a Daher re-add
 * after native cancellation on the same accession now fails (a unique-constraint violation,
 * surfaced loudly by {@code DaherAddedTestTransaction.addTest}), rather than reviving silently.
 * {@code correlation_value} remains independently unique as well -- see the migration for both
 * exact constraints.
 */
@Entity
@Table(name = "daher_analysis_correlation", uniqueConstraints = {
        @UniqueConstraint(name = "daher_analysis_correlation_value_uk", columnNames = "correlation_value"),
        @UniqueConstraint(name = "daher_analysis_correlation_analysis_uk", columnNames = "analysis_id") })
public class DaherAnalysisCorrelation extends BaseObject<Integer> {

    private static final long serialVersionUID = 1L;

    @Id
    @GeneratedValue(strategy = GenerationType.SEQUENCE, generator = "daher_analysis_correlation_seq_gen")
    @SequenceGenerator(name = "daher_analysis_correlation_seq_gen", sequenceName = "daher_analysis_correlation_seq", allocationSize = 1)
    @Column(name = "id")
    private Integer id;

    // Scalar column, not a @ManyToOne -- Analysis is mapped via hbm.xml, not JPA annotations, and
    // every other reader of this value (searchDaherCorrelationNatively, the probe) only ever needs
    // the id to call AnalysisService.get(String.valueOf(...)), never a navigable object graph. The
    // DB-level FK (see the migration) is what actually enforces referential integrity, independent
    // of how the Java side chooses to model it.
    //
    // Long, not String: clinlims.analysis.id is numeric(10,0); Analysis's own hbm.xml mapping
    // exposes it as a Java String only via a custom Hibernate UserType (LIMSStringNumberUserType)
    // registered on that specific hbm.xml property, which a plain JPA @Column on this annotated
    // entity does not have access to. Mapping the DB's actual numeric type directly and converting
    // to/from String only at the AnalysisService call boundary avoids depending on that UserType
    // being reachable from annotation-based mappings, which is not established anywhere else in
    // this codebase.
    @Column(name = "analysis_id", nullable = false)
    private Long analysisId;

    @Column(name = "correlation_value", nullable = false, length = 64)
    private String correlationValue;

    @Column(name = "created_at", nullable = false)
    private Timestamp createdAt;

    public DaherAnalysisCorrelation() {
        super();
    }

    @Override
    public Integer getId() {
        return id;
    }

    @Override
    public void setId(Integer id) {
        this.id = id;
    }

    public Long getAnalysisId() {
        return analysisId;
    }

    public void setAnalysisId(Long analysisId) {
        this.analysisId = analysisId;
    }

    public String getCorrelationValue() {
        return correlationValue;
    }

    public void setCorrelationValue(String correlationValue) {
        this.correlationValue = correlationValue;
    }

    public Timestamp getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(Timestamp createdAt) {
        this.createdAt = createdAt;
    }
}
