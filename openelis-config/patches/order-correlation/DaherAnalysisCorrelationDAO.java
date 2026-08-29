package org.openelisglobal.dataexchange.fhir.dao;

import java.util.List;
import org.openelisglobal.common.dao.BaseDAO;
import org.openelisglobal.dataexchange.fhir.DaherAnalysisCorrelation;

public interface DaherAnalysisCorrelationDAO extends BaseDAO<DaherAnalysisCorrelation, Integer> {

    List<DaherAnalysisCorrelation> getByCorrelationValue(String correlationValue);

    List<DaherAnalysisCorrelation> getByAnalysisId(Long analysisId);
}
