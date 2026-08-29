package org.openelisglobal.dataexchange.fhir.daoimpl;

import jakarta.persistence.TypedQuery;
import java.util.List;
import org.openelisglobal.common.daoimpl.BaseDAOImpl;
import org.openelisglobal.dataexchange.fhir.DaherAnalysisCorrelation;
import org.openelisglobal.dataexchange.fhir.dao.DaherAnalysisCorrelationDAO;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

@Component
@Transactional
public class DaherAnalysisCorrelationDAOImpl extends BaseDAOImpl<DaherAnalysisCorrelation, Integer>
        implements DaherAnalysisCorrelationDAO {

    public DaherAnalysisCorrelationDAOImpl() {
        super(DaherAnalysisCorrelation.class);
    }

    @Override
    @Transactional(readOnly = true)
    public List<DaherAnalysisCorrelation> getByCorrelationValue(String correlationValue) {
        String jpql = "FROM DaherAnalysisCorrelation c WHERE c.correlationValue = :correlationValue";
        TypedQuery<DaherAnalysisCorrelation> query = entityManager.createQuery(jpql, DaherAnalysisCorrelation.class);
        query.setParameter("correlationValue", correlationValue);
        return query.getResultList();
    }

    @Override
    @Transactional(readOnly = true)
    public List<DaherAnalysisCorrelation> getByAnalysisId(Long analysisId) {
        String jpql = "FROM DaherAnalysisCorrelation c WHERE c.analysisId = :analysisId";
        TypedQuery<DaherAnalysisCorrelation> query = entityManager.createQuery(jpql, DaherAnalysisCorrelation.class);
        query.setParameter("analysisId", analysisId);
        return query.getResultList();
    }
}
