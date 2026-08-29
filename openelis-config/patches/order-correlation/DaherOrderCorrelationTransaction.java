package org.openelisglobal.dataexchange.fhir;

import jakarta.servlet.http.HttpServletRequest;
import org.openelisglobal.patient.action.bean.PatientManagementInfo;
import org.openelisglobal.sample.action.util.SamplePatientUpdateData;
import org.openelisglobal.sample.form.SamplePatientEntryForm;
import org.openelisglobal.sample.service.PatientManagementUpdate;
import org.openelisglobal.sample.service.SamplePatientEntryService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/**
 * DH-011D3 (independent review, P0 finding A): the single outer transaction covering a Daher
 * FHIR ServiceRequest create's entire native-write sequence -- patient/sample/sample_item
 * /analysis (via {@link SamplePatientEntryService#persistData}) and the correlation
 * (electronic_order + sample.clinicalOrderId, via the caller-supplied {@code persistCorrelation}
 * callback) -- so a failure persisting the correlation, or a race the partial unique index
 * rejects, rolls back the native order too instead of leaving it orphaned with no correlation.
 *
 * <p><b>Why this lives in its own bean instead of on {@code ServiceRequestProvider
 * .createServiceRequest} directly.</b> {@code ServiceRequestProvider} implements
 * {@code IResourceProvider}, and HAPI's {@code FhirRestfulServer} finds the {@code @Create}/
 * {@code @Search}/{@code @Update}/{@code @Delete} methods it dispatches to by reflecting on the
 * bean instance it receives. Putting {@code @Transactional} directly on
 * {@code createServiceRequest} makes Spring wrap that bean in a JDK dynamic proxy (it implements
 * an interface); that proxy exposes only {@code IResourceProvider}'s own methods, none of which
 * carry HAPI's annotations -- those live on the concrete class. The result, confirmed live
 * during this pass: {@code HAPI-0289: Did not find any annotated RESTful methods}, and the
 * entire provider silently stops serving every operation, not just create. This bean is never
 * reflected on by HAPI, so it can be proxied however Spring likes without breaking anything.
 */
@Component
public class DaherOrderCorrelationTransaction {

    @Autowired
    private SamplePatientEntryService samplePatientService;

    @Transactional(rollbackFor = Exception.class)
    public void persist(SamplePatientUpdateData updateData, PatientManagementUpdate patientUpdate,
            PatientManagementInfo patientInfo, SamplePatientEntryForm form, HttpServletRequest request,
            Runnable persistCorrelation) {
        samplePatientService.persistData(updateData, patientUpdate, patientInfo, form, request);
        persistCorrelation.run();
    }
}
