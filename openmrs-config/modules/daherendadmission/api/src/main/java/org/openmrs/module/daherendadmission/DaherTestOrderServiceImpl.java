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

import static org.openmrs.module.daherendadmission.DaherEndAdmissionConstants.ADMISSION_ENCOUNTER_TYPE_UUID;
import static org.openmrs.module.daherendadmission.DaherEndAdmissionConstants.CLINICIAN_ADMISSION_ROLE;
import static org.openmrs.module.daherendadmission.DaherEndAdmissionConstants.MATERNITE_LOCATION_UUID;
import static org.openmrs.module.daherendadmission.DaherEndAdmissionConstants.MATERNITY_ADMISSION_VISIT_TYPE_UUID;

import java.util.Collection;

import org.openmrs.CareSetting;
import org.openmrs.Concept;
import org.openmrs.Encounter;
import org.openmrs.Order;
import org.openmrs.OrderType;
import org.openmrs.Provider;
import org.openmrs.TestOrder;
import org.openmrs.User;
import org.openmrs.Visit;
import org.openmrs.api.OrderContext;
import org.openmrs.api.OrderService;
import org.openmrs.api.context.Context;
import org.openmrs.util.PrivilegeConstants;
import org.springframework.transaction.annotation.Transactional;

/** Standard OpenMRS TestOrder creation behind an intentionally non-generic request boundary. */
@Transactional(rollbackFor = Exception.class)
public class DaherTestOrderServiceImpl implements DaherTestOrderService {

	private DaherProxyPrivilegeExecutor privilegeExecutor = new DaherProxyPrivilegeExecutor();

	public void setPrivilegeExecutor(DaherProxyPrivilegeExecutor privilegeExecutor) {
		this.privilegeExecutor = privilegeExecutor;
	}

	@Override
	public DaherTestOrderResult create(DaherTestOrderRequest request) {
		User caller = Context.getAuthenticatedUser();
		if (caller == null || !caller.hasRole(CLINICIAN_ADMISSION_ROLE)
		        || !Context.hasPrivilege(PrivilegeConstants.ADD_ORDERS)) {
			return result(DaherTestOrderOutcome.UNAUTHORIZED);
		}

		Collection<Provider> providers = privilegeExecutor.execute(PrivilegeConstants.GET_PROVIDERS,
		    () -> Context.getProviderService().getProvidersByPerson(caller.getPerson(), false));
		DaherTestOrderOutcome providerFailure = providerFailure(providers);
		if (providerFailure != null) {
			return result(providerFailure);
		}
		Provider orderer = providers.iterator().next();

		Encounter encounter = Context.getEncounterService().getEncounterByUuid(request.getEncounterUuid());
		if (encounter == null) {
			return result(DaherTestOrderOutcome.ENCOUNTER_NOT_FOUND);
		}
		if (!isEligibleEncounter(encounter)) {
			return result(DaherTestOrderOutcome.INELIGIBLE_ENCOUNTER);
		}

		Concept concept = privilegeExecutor.execute(PrivilegeConstants.GET_CONCEPTS,
		    () -> Context.getConceptService().getConceptByUuid(request.getConceptUuid()));
		if (concept == null || Boolean.TRUE.equals(concept.getRetired())) {
			return result(DaherTestOrderOutcome.CONCEPT_NOT_FOUND);
		}

		OrderService orderService = Context.getOrderService();
		CareSetting careSetting = privilegeExecutor.execute(PrivilegeConstants.GET_CARE_SETTINGS,
		    () -> orderService.getCareSettingByUuid(request.getCareSettingUuid()));
		if (careSetting == null || Boolean.TRUE.equals(careSetting.getRetired())) {
			return result(DaherTestOrderOutcome.CARE_SETTING_NOT_FOUND);
		}

		OrderType testOrderType = privilegeExecutor.execute(PrivilegeConstants.GET_ORDER_TYPES,
		    () -> orderService.getOrderTypeByUuid(OrderType.TEST_ORDER_TYPE_UUID));
		if (testOrderType == null || Boolean.TRUE.equals(testOrderType.getRetired())
		        || !TestOrder.class.equals(testOrderType.getJavaClass())) {
			return result(DaherTestOrderOutcome.TEST_ORDER_TYPE_UNAVAILABLE);
		}

		TestOrder order = new TestOrder();
		order.setEncounter(encounter);
		order.setPatient(encounter.getPatient());
		order.setConcept(concept);
		order.setCareSetting(careSetting);
		order.setOrderer(orderer);
		order.setOrderType(testOrderType);

		Order saved = orderService.saveOrder(order, new OrderContext());
		return new DaherTestOrderResult(DaherTestOrderOutcome.CREATED, saved.getUuid(), saved.getOrderNumber());
	}

	static boolean isEligibleEncounter(Encounter encounter) {
		if (encounter == null || Boolean.TRUE.equals(encounter.getVoided()) || encounter.getEncounterType() == null
		        || !ADMISSION_ENCOUNTER_TYPE_UUID.equals(encounter.getEncounterType().getUuid())
		        || encounter.getLocation() == null || !MATERNITE_LOCATION_UUID.equals(encounter.getLocation().getUuid())) {
			return false;
		}
		Visit visit = encounter.getVisit();
		return visit != null && visit.getStopDatetime() == null && !Boolean.TRUE.equals(visit.getVoided())
		        && visit.getVisitType() != null && MATERNITY_ADMISSION_VISIT_TYPE_UUID.equals(visit.getVisitType().getUuid())
		        && visit.getLocation() != null && MATERNITE_LOCATION_UUID.equals(visit.getLocation().getUuid())
		        && visit.getPatient() != null && visit.getPatient().equals(encounter.getPatient());
	}

	static DaherTestOrderOutcome providerFailure(Collection<Provider> providers) {
		if (providers == null || providers.isEmpty()) {
			return DaherTestOrderOutcome.PROVIDER_NOT_FOUND;
		}
		return providers.size() == 1 ? null : DaherTestOrderOutcome.AMBIGUOUS_PROVIDER;
	}

	private static DaherTestOrderResult result(DaherTestOrderOutcome outcome) {
		return new DaherTestOrderResult(outcome, null, null);
	}
}
