package org.openelisglobal.dataexchange.fhir.daoimpl;

import jakarta.persistence.TypedQuery;
import java.util.List;
import org.openelisglobal.common.daoimpl.BaseDAOImpl;
import org.openelisglobal.dataexchange.fhir.DaherPatientIdentity;
import org.openelisglobal.dataexchange.fhir.dao.DaherPatientIdentityDAO;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

@Component
@Transactional
public class DaherPatientIdentityDAOImpl extends BaseDAOImpl<DaherPatientIdentity, Long>
        implements DaherPatientIdentityDAO {

    public DaherPatientIdentityDAOImpl() {
        super(DaherPatientIdentity.class);
    }

    @Override
    @Transactional(readOnly = true)
    public List<DaherPatientIdentity> getBySystemAndValue(String identifierSystem, String identifierValue) {
        String jpql = "FROM DaherPatientIdentity d WHERE d.identifierSystem = :identifierSystem "
                + "AND d.identifierValue = :identifierValue";
        TypedQuery<DaherPatientIdentity> query = entityManager.createQuery(jpql, DaherPatientIdentity.class);
        query.setParameter("identifierSystem", identifierSystem);
        query.setParameter("identifierValue", identifierValue);
        return query.getResultList();
    }

    @Override
    @Transactional(readOnly = true)
    public List<DaherPatientIdentity> getByPatientId(Long patientId) {
        String jpql = "FROM DaherPatientIdentity d WHERE d.patientId = :patientId";
        TypedQuery<DaherPatientIdentity> query = entityManager.createQuery(jpql, DaherPatientIdentity.class);
        query.setParameter("patientId", patientId);
        return query.getResultList();
    }
}
