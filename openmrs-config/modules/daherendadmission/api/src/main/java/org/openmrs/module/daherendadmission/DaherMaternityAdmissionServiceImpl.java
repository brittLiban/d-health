/*
 * This Source Code Form is subject to the terms of the Mozilla Public License,
 * v. 2.0. If a copy of the MPL was not distributed with this file, You can
 * obtain one at http://mozilla.org/MPL/2.0/. OpenMRS is also distributed under
 * the terms of the Healthcare Disclaimer located at http://openmrs.org/license.
 *
 * Copyright (C) OpenMRS Inc. OpenMRS is a registered trademark and the OpenMRS
 * graphic logo is a trademark of OpenMRS Inc.
 */
package org.openmrs.module.daherendadmission;

import java.util.Collection;
import java.util.Date;

import org.openmrs.Provider;
import org.openmrs.User;
import org.openmrs.Visit;
import org.openmrs.VisitAttribute;
import org.openmrs.VisitAttributeType;
import org.openmrs.api.VisitService;
import org.openmrs.api.context.Context;
import org.openmrs.module.daherendadmission.db.DaherEndAdmissionDao;
import org.springframework.transaction.annotation.Transactional;

import static org.openmrs.module.daherendadmission.DaherEndAdmissionConstants.AUDIT_VISIT_ATTRIBUTE_TYPE_UUID;
import static org.openmrs.module.daherendadmission.DaherEndAdmissionConstants.CLINICIAN_ADMISSION_ROLE;
import static org.openmrs.module.daherendadmission.DaherEndAdmissionConstants.MATERNITE_LOCATION_UUID;
import static org.openmrs.module.daherendadmission.DaherEndAdmissionConstants.MATERNITY_ADMISSION_VISIT_TYPE_UUID;

/**
 * Registered via moduleApplicationContext.xml as a TransactionProxyFactoryBean, wired exactly the
 * way OpenMRS core and bedmanagement wire their own services (preInterceptors=serviceInterceptors,
 * the same shared transactionManager/transactionAttributeSource beans core registers). Must always
 * be invoked through that proxy (Context.getRegisteredComponent(...), never `new
 * DaherMaternityAdmissionServiceImpl()`) so this method is the outermost transactional entry point
 * -- see the interface javadoc and ADR-030 for why.
 */
@Transactional
public class DaherMaternityAdmissionServiceImpl implements DaherMaternityAdmissionService {

	private DaherEndAdmissionDao dao;

	public void setDao(DaherEndAdmissionDao dao) {
		this.dao = dao;
	}

	@Override
	public EndAdmissionResult endAdmission(String visitUuid, String initiatingUserUuid) {
		User initiator = Context.getUserService().getUserByUuid(initiatingUserUuid);
		if (!isAuthorizedInitiator(initiator)) {
			// Checked here, inside the daemon-elevated transaction, not in the servlet's
			// un-elevated request thread: Get Providers is deliberately not among the canonical
			// Daher Clinician Admission role's privileges (ADR-030 forbids granting new broad
			// privileges), so getProvidersByPerson can only be called under daemon elevation.
			// initiatingUserUuid is the real caller's identity, captured by the servlet BEFORE
			// elevation -- this method never derives identity from Context.getAuthenticatedUser(),
			// which inside a daemon thread is the daemon user, not the caller.
			return new EndAdmissionResult(EndAdmissionOutcome.UNAUTHORIZED, visitUuid, null);
		}

		Visit visit = dao.getVisitByUuidForUpdate(visitUuid);
		if (visit == null) {
			return new EndAdmissionResult(EndAdmissionOutcome.VISIT_NOT_FOUND, visitUuid, null);
		}
		if (visit.getVisitType() == null || !MATERNITY_ADMISSION_VISIT_TYPE_UUID.equals(visit.getVisitType().getUuid())) {
			return new EndAdmissionResult(EndAdmissionOutcome.WRONG_VISIT_TYPE, visitUuid, null);
		}
		if (visit.getLocation() == null || !MATERNITE_LOCATION_UUID.equals(visit.getLocation().getUuid())) {
			return new EndAdmissionResult(EndAdmissionOutcome.WRONG_LOCATION, visitUuid, null);
		}
		if (visit.getStopDatetime() != null) {
			// Idempotent: the pessimistic lock above means a concurrent second caller only reaches
			// this line after the first caller's transaction has already committed, so this reflects
			// truly authoritative state, not a stale read racing the first write.
			return new EndAdmissionResult(EndAdmissionOutcome.ALREADY_ENDED, visitUuid, visit.getStopDatetime());
		}

		VisitService visitService = Context.getVisitService();
		Date now = new Date();

		visit.setStopDatetime(now);
		// Best-effort only: OpenMRS's own RequiredDataAdvice overwrites changedBy/dateChanged from
		// the daemon-context authenticated user regardless of what is set here (proven in UI-015D).
		// The VisitAttribute below, not this, is the durable attribution record.
		visit.setChangedBy(initiator);
		visit.setDateChanged(now);

		VisitAttributeType auditType = visitService.getVisitAttributeTypeByUuid(AUDIT_VISIT_ATTRIBUTE_TYPE_UUID);
		if (auditType != null) {
			VisitAttribute audit = new VisitAttribute();
			audit.setAttributeType(auditType);
			audit.setValueReferenceInternal("operationType=END_DAHER_MATERNITY_ADMISSION;initiatingUserUuid="
			        + initiatingUserUuid + ";timestamp=" + now.getTime());
			visit.addAttribute(audit);
		}

		// This call, and everything it cascades into inside bedmanagement/queue's own SaveHandlers,
		// runs inside the transaction this method's own proxy already opened -- proven in UI-015D
		// (TransactionSynchronizationManager.isActualTransactionActive()==true at this point), which
		// is what makes bedmanagement's/queue's independent @Transactional calls join this one
		// instead of committing separately.
		visitService.saveVisit(visit);

		return new EndAdmissionResult(EndAdmissionOutcome.ENDED, visitUuid, visit.getStopDatetime());
	}

	/**
	 * Reception must fail (no Daher Clinician Admission role). A user holding Daher Clinician Admission
	 * but no Provider record (the ADR-017E2/ADR-017F "missing Provider" fail-closed case) must also
	 * fail -- checked via an actual Provider record rather than the generic "Provider" role, matching
	 * how the rest of this application already gates clinical-actor behavior on currentProvider !=
	 * null, not on role membership alone. Must run under daemon elevation (see caller) since
	 * getProvidersByPerson requires the Get Providers privilege, which the canonical role deliberately
	 * does not grant.
	 */
	private boolean isAuthorizedInitiator(User initiator) {
		if (initiator == null || !initiator.hasRole(CLINICIAN_ADMISSION_ROLE)) {
			return false;
		}
		Collection<Provider> providers = Context.getProviderService().getProvidersByPerson(initiator.getPerson(), false);
		return providers != null && !providers.isEmpty();
	}
}
