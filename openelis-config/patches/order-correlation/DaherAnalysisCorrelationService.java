package org.openelisglobal.dataexchange.fhir.service;

import java.util.List;
import org.openelisglobal.common.service.BaseObjectService;
import org.openelisglobal.dataexchange.fhir.DaherAnalysisCorrelation;

public interface DaherAnalysisCorrelationService extends BaseObjectService<DaherAnalysisCorrelation, Integer> {

    List<DaherAnalysisCorrelation> getByCorrelationValue(String correlationValue);

    List<DaherAnalysisCorrelation> getByAnalysisId(Long analysisId);
}
