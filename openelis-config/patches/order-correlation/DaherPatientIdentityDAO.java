package org.openelisglobal.dataexchange.fhir.dao;

import java.util.List;
import org.openelisglobal.common.dao.BaseDAO;
import org.openelisglobal.dataexchange.fhir.DaherPatientIdentity;

public interface DaherPatientIdentityDAO extends BaseDAO<DaherPatientIdentity, Long> {

    List<DaherPatientIdentity> getBySystemAndValue(String identifierSystem, String identifierValue);

    List<DaherPatientIdentity> getByPatientId(Long patientId);
}
