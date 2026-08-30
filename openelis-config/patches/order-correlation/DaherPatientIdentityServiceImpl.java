package org.openelisglobal.dataexchange.fhir.service;

import java.util.List;
import org.openelisglobal.common.dao.BaseDAO;
import org.openelisglobal.common.service.BaseObjectServiceImpl;
import org.openelisglobal.dataexchange.fhir.DaherPatientIdentity;
import org.openelisglobal.dataexchange.fhir.dao.DaherPatientIdentityDAO;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class DaherPatientIdentityServiceImpl extends BaseObjectServiceImpl<DaherPatientIdentity, Long>
        implements DaherPatientIdentityService {

    @Autowired
    private DaherPatientIdentityDAO daherPatientIdentityDAO;

    public DaherPatientIdentityServiceImpl() {
        super(DaherPatientIdentity.class);
    }

    @Override
    protected BaseDAO<DaherPatientIdentity, Long> getBaseObjectDAO() {
        return daherPatientIdentityDAO;
    }

    @Override
    @Transactional(readOnly = true)
    public List<DaherPatientIdentity> getBySystemAndValue(String identifierSystem, String identifierValue) {
        return daherPatientIdentityDAO.getBySystemAndValue(identifierSystem, identifierValue);
    }

    @Override
    @Transactional(readOnly = true)
    public List<DaherPatientIdentity> getByPatientId(Long patientId) {
        return daherPatientIdentityDAO.getByPatientId(patientId);
    }
}
