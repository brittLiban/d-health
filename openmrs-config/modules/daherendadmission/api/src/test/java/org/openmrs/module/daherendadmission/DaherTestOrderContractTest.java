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

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.lang.reflect.Method;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;

import org.junit.Test;
import org.openmrs.Encounter;
import org.openmrs.EncounterType;
import org.openmrs.Location;
import org.openmrs.Patient;
import org.openmrs.Privilege;
import org.openmrs.Provider;
import org.openmrs.Role;
import org.openmrs.Visit;
import org.openmrs.VisitType;
import org.openmrs.annotation.Authorized;
import org.openmrs.module.initializer.Domain;
import org.openmrs.util.PrivilegeConstants;

public class DaherTestOrderContractTest {

	@Test
	public void create_requiresCallerOwnedAddOrdersPrivilege() throws Exception {
		Method create = DaherTestOrderService.class.getMethod("create", DaherTestOrderRequest.class);
		Authorized authorized = create.getAnnotation(Authorized.class);
		assertEquals(1, authorized.value().length);
		assertEquals(PrivilegeConstants.ADD_ORDERS, authorized.value()[0]);
	}

	@Test
	public void providerSelectionFailsClosedForZeroOneAndMany() {
		Provider provider = new Provider();
		assertEquals(DaherTestOrderOutcome.PROVIDER_NOT_FOUND,
		    DaherTestOrderServiceImpl.providerFailure(Collections.emptyList()));
		assertNull(DaherTestOrderServiceImpl.providerFailure(Collections.singletonList(provider)));
		assertEquals(DaherTestOrderOutcome.AMBIGUOUS_PROVIDER,
		    DaherTestOrderServiceImpl.providerFailure(Arrays.asList(provider, new Provider())));
	}

	@Test
	public void encounterEligibilityIsTheExistingActiveMaternityAdmissionContract() {
		Encounter encounter = eligibleEncounter();
		assertTrue(DaherTestOrderServiceImpl.isEligibleEncounter(encounter));

		encounter.getVisit().setStopDatetime(new java.util.Date());
		assertFalse(DaherTestOrderServiceImpl.isEligibleEncounter(encounter));
	}

	@Test
	public void encounterEligibilityRejectsPatientMismatch() {
		Encounter encounter = eligibleEncounter();
		encounter.getVisit().setPatient(new Patient());
		assertFalse(DaherTestOrderServiceImpl.isEligibleEncounter(encounter));
	}

	@Test
	public void externalConfigurationGrantIsDirectIdempotentAndDoesNotMutateAuthenticated() {
		Privilege coreRead = new Privilege(PrivilegeConstants.GET_GLOBAL_PROPERTIES, "internal compatibility");
		Privilege externalRead = new Privilege(DaherEndAdmissionConstants.VIEW_SYSTEM_CONFIGURATION_PRIVILEGE,
		        "external configuration");
		Role authenticated = new Role("Authenticated");
		authenticated.setPrivileges(new HashSet<Privilege>());
		authenticated.addPrivilege(coreRead);
		Role sysAdmin = new Role(DaherEndAdmissionConstants.SYS_ADMIN_ROLE);
		sysAdmin.setPrivileges(new HashSet<Privilege>());

		assertTrue(DaherExternalConfigurationPrivilegeProvisioner.addDirectPrivilege(sysAdmin, externalRead));
		assertTrue(sysAdmin.getPrivileges().contains(externalRead));
		assertFalse(DaherExternalConfigurationPrivilegeProvisioner.addDirectPrivilege(sysAdmin, externalRead));
		assertTrue(authenticated.getPrivileges().contains(coreRead));
		assertFalse(authenticated.getPrivileges().contains(externalRead));
	}

	@Test
	public void externalConfigurationGrantRemovesInitializerWildcardCopies() {
		Privilege externalRead = new Privilege(DaherEndAdmissionConstants.VIEW_SYSTEM_CONFIGURATION_PRIVILEGE,
		        "external configuration");
		Role wildcardRole = new Role("Privilege Level: Full");
		wildcardRole.setPrivileges(new HashSet<Privilege>());
		wildcardRole.addPrivilege(externalRead);

		assertTrue(DaherExternalConfigurationPrivilegeProvisioner.removeDirectPrivilege(wildcardRole, externalRead));
		assertFalse(wildcardRole.getPrivileges().contains(externalRead));
		assertFalse(DaherExternalConfigurationPrivilegeProvisioner.removeDirectPrivilege(wildcardRole, externalRead));
	}

	@Test
	public void externalConfigurationGrantLoaderRunsAfterInitializerRoles() {
		DaherExternalConfigurationPrivilegeLoader loader = new DaherExternalConfigurationPrivilegeLoader();
		assertEquals("roles", loader.getDomainName());
		assertTrue(loader.getOrder() > Domain.ROLES.getOrder());
		assertFalse(loader.isPreLoader());
	}

	private static Encounter eligibleEncounter() {
		Patient patient = new Patient();
		patient.setUuid("11111111-1111-1111-1111-111111111111");

		Location location = new Location();
		location.setUuid(DaherEndAdmissionConstants.MATERNITE_LOCATION_UUID);
		VisitType visitType = new VisitType();
		visitType.setUuid(DaherEndAdmissionConstants.MATERNITY_ADMISSION_VISIT_TYPE_UUID);
		Visit visit = new Visit();
		visit.setPatient(patient);
		visit.setLocation(location);
		visit.setVisitType(visitType);

		EncounterType encounterType = new EncounterType();
		encounterType.setUuid(DaherEndAdmissionConstants.ADMISSION_ENCOUNTER_TYPE_UUID);
		Encounter encounter = new Encounter();
		encounter.setPatient(patient);
		encounter.setLocation(location);
		encounter.setEncounterType(encounterType);
		encounter.setVisit(visit);
		return encounter;
	}
}
