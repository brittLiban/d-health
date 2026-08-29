package org.openelisglobal.dataexchange.fhir.service;

import java.util.List;
import org.openelisglobal.common.dao.BaseDAO;
import org.openelisglobal.common.service.BaseObjectServiceImpl;
import org.openelisglobal.dataexchange.fhir.DaherAnalysisCorrelation;
import org.openelisglobal.dataexchange.fhir.dao.DaherAnalysisCorrelationDAO;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class DaherAnalysisCorrelationServiceImpl extends BaseObjectServiceImpl<DaherAnalysisCorrelation, Integer>
        implements DaherAnalysisCorrelationService {

    @Autowired
    private DaherAnalysisCorrelationDAO daherAnalysisCorrelationDAO;

    public DaherAnalysisCorrelationServiceImpl() {
        super(DaherAnalysisCorrelation.class);
    }

    @Override
    protected BaseDAO<DaherAnalysisCorrelation, Integer> getBaseObjectDAO() {
        return daherAnalysisCorrelationDAO;
    }

    @Override
    @Transactional(readOnly = true)
    public List<DaherAnalysisCorrelation> getByCorrelationValue(String correlationValue) {
        return daherAnalysisCorrelationDAO.getByCorrelationValue(correlationValue);
    }

    @Override
    @Transactional(readOnly = true)
    public List<DaherAnalysisCorrelation> getByAnalysisId(Long analysisId) {
        return daherAnalysisCorrelationDAO.getByAnalysisId(analysisId);
    }
}
