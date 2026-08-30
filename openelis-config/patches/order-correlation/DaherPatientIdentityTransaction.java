package org.openelisglobal.dataexchange.fhir;

import java.sql.Timestamp;
import org.openelisglobal.common.util.DateUtil;
import org.openelisglobal.dataexchange.fhir.service.DaherPatientIdentityService;
import org.openelisglobal.patient.action.bean.PatientManagementInfo;
import org.openelisglobal.patient.service.PatientService;
import org.openelisglobal.patient.valueholder.Patient;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/**
 * DH-011G2 §9: the single outer transaction covering a Daher-linked Patient create's entire
 * native-write sequence -- the native {@code Patient}/{@code Person} rows (via
 * {@link PatientService#persistPatientData}) and the {@code daher_patient_identity} linkage row --
 * so a failure persisting the linkage, or a race the DB-level unique indexes reject, rolls the
 * native patient write back too instead of leaving a Patient with no linkage (or, symmetrically,
 * never leaves a linkage row pointing at a Patient that was never actually committed).
 *
 * <p><b>Why this lives in its own bean instead of on {@code PatientProvider.createPatient}
 * directly.</b> Identical reasoning to {@link DaherOrderCorrelationTransaction}, whose javadoc has
 * the full, live-confirmed account: {@code PatientProvider} implements {@code IResourceProvider},
 * and putting {@code @Transactional} directly on one of its HAPI-dispatched methods makes Spring
 * wrap the bean in a JDK dynamic proxy that exposes only the interface's methods -- none of which
 * carry HAPI's {@code @Create}/{@code @Search}/{@code @Update}/{@code @Delete} annotations, which
 * live on the concrete class. HAPI's reflection-based dispatch would then find no annotated
 * methods and the entire provider would silently stop serving every operation. This bean is never
 * reflected on by HAPI, so it can be proxied however Spring likes without breaking anything.
 */
@Component
public class DaherPatientIdentityTransaction {

    @Autowired
    private PatientService patientService;

    @Autowired
    private DaherPatientIdentityService daherPatientIdentityService;

    /**
     * Persists the native Patient, then the Daher linkage row, in one transaction. {@code patient}
     * is mutated in place by {@code persistPatientData} (its generated id becomes available after
     * this call returns), exactly matching how {@code PatientProvider.createPatient} already reads
     * {@code patient.getId()}/{@code patient.getFhirUuidAsString()} after the call it used to make
     * directly.
     *
     * <p>The DB's own unique indexes ({@code daher_patient_identity_system_value_uk},
     * {@code daher_patient_identity_patient_system_uk}) are the authoritative guarantee against a
     * racing duplicate -- a violation surfaces here as a real Postgres constraint-violation
     * exception, which {@code rollbackFor = Exception.class} rolls the native Patient write back
     * for too. {@code PatientProvider}'s own fast, non-authoritative pre-check (a native search
     * before this method is ever called) exists only to give the common, non-racing case a clean
     * error message -- it is not what makes a duplicate impossible.
     */
    @Transactional(rollbackFor = Exception.class)
    public void persist(PatientManagementInfo patientInfo, Patient patient, String sysUserId,
            String openmrsPatientUuid) {
        patientService.persistPatientData(patientInfo, patient, sysUserId);

        DaherPatientIdentity link = new DaherPatientIdentity();
        link.setPatientId(Long.valueOf(patient.getId()));
        link.setIdentifierSystem(DaherPatientIdentity.SYSTEM);
        link.setIdentifierValue(openmrsPatientUuid);
        Timestamp now = DateUtil.getNowAsTimestamp();
        link.setCreatedAt(now);
        link.setSysUserId(sysUserId);
        daherPatientIdentityService.insert(link);
    }
}
