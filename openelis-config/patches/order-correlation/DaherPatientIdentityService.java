package org.openelisglobal.dataexchange.fhir.service;

import java.util.List;
import org.openelisglobal.common.service.BaseObjectService;
import org.openelisglobal.dataexchange.fhir.DaherPatientIdentity;

public interface DaherPatientIdentityService extends BaseObjectService<DaherPatientIdentity, Long> {

    List<DaherPatientIdentity> getBySystemAndValue(String identifierSystem, String identifierValue);

    List<DaherPatientIdentity> getByPatientId(Long patientId);
}
