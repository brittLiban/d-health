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
 * DH-011G2 (LIBAN DECISION: SELECT OPTION D): the Daher-owned native linkage between an OpenMRS
 * Patient UUID (the source-system identity) and an OpenELIS Patient. See
 * {@code 006-daher-patient-identity.xml} for the full account of why this exists as a standalone
 * table -- in short, DH-011G proved that OpenELIS's FHIR Patient ingestion recognizes exactly four
 * hardcoded identifier systems (none of which this value may reuse -- see the DECISIONS.md ADR:
 * {@code pat_nationalId} etc. keep their own native meanings) and that {@code Patient.id}/
 * {@code pat_guid}/{@code pat_uuid} are always server-generated on create, so neither existing
 * mechanism can carry an externally-supplied identity.
 *
 * <p><b>{@code SYSTEM} is the one value V1 Java code ever writes or reads on this table</b> --
 * {@code identifier_system} is a generic column at the schema level (see the migration's javadoc
 * for why), but this class is the single source of truth for which system string V1 recognizes,
 * exactly mirroring how {@link DaherOrderCorrelation#SYSTEM} is the analogous constant for
 * ServiceRequest correlation.
 *
 * <p><b>Uniqueness is enforced at the database</b> (see the migration): {@code (identifier_system,
 * identifier_value)} so one OpenMRS UUID can never link to two OpenELIS Patients, and
 * {@code (patient_id, identifier_system)} so one OpenELIS Patient can never carry two different
 * OpenMRS UUIDs under this system -- together a true one-to-one mapping. Neither is optional or
 * merely advisory; both are real Postgres unique indexes a racing insert will fail against.
 */
@Entity
@Table(name = "daher_patient_identity", uniqueConstraints = {
        @UniqueConstraint(name = "daher_patient_identity_system_value_uk", columnNames = { "identifier_system",
                "identifier_value" }),
        @UniqueConstraint(name = "daher_patient_identity_patient_system_uk", columnNames = { "patient_id",
                "identifier_system" }) })
public class DaherPatientIdentity extends BaseObject<Long> {

    private static final long serialVersionUID = 1L;

    /** The one identifier system V1 ever writes -- see this class's javadoc. */
    public static final String SYSTEM = "urn:daher-health:openmrs-patient-uuid";

    @Id
    @GeneratedValue(strategy = GenerationType.SEQUENCE, generator = "daher_patient_identity_seq_gen")
    @SequenceGenerator(name = "daher_patient_identity_seq_gen", sequenceName = "daher_patient_identity_seq", allocationSize = 1)
    @Column(name = "id")
    private Long id;

    // Scalar column, not a @ManyToOne -- Patient is mapped via hbm.xml, not JPA annotations, and
    // every reader of this value only ever needs the id to call PatientService.get(String
    // .valueOf(...)), never a navigable object graph. The DB-level FK (see the migration) is what
    // actually enforces referential integrity.
    //
    // Long, not String: clinlims.patient.id is numeric(10,0); Patient's own hbm.xml mapping
    // exposes it as a Java String only via a custom Hibernate UserType (LIMSStringNumberUserType)
    // registered on that specific hbm.xml property, which a plain JPA @Column on this annotated
    // entity does not have access to -- the identical reasoning DaherAnalysisCorrelation.analysisId
    // already documents for the same class of column on a different table.
    @Column(name = "patient_id", nullable = false)
    private Long patientId;

    @Column(name = "identifier_system", nullable = false, length = 255)
    private String identifierSystem;

    @Column(name = "identifier_value", nullable = false, length = 255)
    private String identifierValue;

    @Column(name = "created_at", nullable = false)
    private Timestamp createdAt;

    public DaherPatientIdentity() {
        super();
    }

    @Override
    public Long getId() {
        return id;
    }

    @Override
    public void setId(Long id) {
        this.id = id;
    }

    public Long getPatientId() {
        return patientId;
    }

    public void setPatientId(Long patientId) {
        this.patientId = patientId;
    }

    public String getIdentifierSystem() {
        return identifierSystem;
    }

    public void setIdentifierSystem(String identifierSystem) {
        this.identifierSystem = identifierSystem;
    }

    public String getIdentifierValue() {
        return identifierValue;
    }

    public void setIdentifierValue(String identifierValue) {
        this.identifierValue = identifierValue;
    }

    public Timestamp getCreatedAt() {
        return createdAt;
    }

    public void setCreatedAt(Timestamp createdAt) {
        this.createdAt = createdAt;
    }
}
