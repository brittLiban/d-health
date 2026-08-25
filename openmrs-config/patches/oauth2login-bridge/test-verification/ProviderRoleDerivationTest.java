/*
 * This Source Code Form is subject to the terms of the Mozilla Public License,
 * v. 2.0. If a copy of the MPL was not distributed with this file, You can
 * obtain one at http://mozilla.org/MPL/2.0/. OpenMRS is also distributed under
 * the terms of the Healthcare Disclaimer located at http://openmrs.org/license.
 *
 * Copyright (C) OpenMRS Inc. OpenMRS is a registered trademark and the OpenMRS
 * graphic logo is a trademark of OpenMRS Inc.
 */
package org.openmrs.module.oauth2login.web.controller;

import java.util.Properties;

import org.junit.Assert;
import org.junit.Test;
import org.openmrs.module.oauth2login.authscheme.UserInfo;

/**
 * DAHER PATCH (ADR-017E2/DH-008G): focused unit tests for
 * {@link OAuth2LoginController#isProviderRoleGranted(UserInfo)}, the method that replaced
 * upstream's fail-open "provider" claim check with a role-derived, fail-closed decision. Each
 * case exercises the real {@link UserInfo} JSON-parsing path (no mocking of that layer) so the
 * roles-claim shape itself is genuinely re-parsed, not assumed. Calls the package-private method
 * directly (same package, no reflection) rather than via a mocking framework.
 */
public class ProviderRoleDerivationTest {

	private static final Properties ROLES_MAPPED_PROPS = rolesMappedProperties();

	private static Properties rolesMappedProperties() {
		Properties props = new Properties();
		props.setProperty(UserInfo.PROP_ROLES, "roles");
		return props;
	}

	private boolean isProviderRoleGranted(String userInfoJson) {
		UserInfo userInfo = new UserInfo(ROLES_MAPPED_PROPS, userInfoJson);
		OAuth2LoginController controller = new OAuth2LoginController();
		return controller.isProviderRoleGranted(userInfo);
	}

	@Test
	public void exactProviderRoleAlone_shouldGrantProvider() throws Exception {
		Assert.assertTrue(isProviderRoleGranted("{\"roles\": [\"Provider\"]}"));
	}

	@Test
	public void exactProviderRoleAmongOthers_shouldGrantProvider() throws Exception {
		Assert.assertTrue(
		    isProviderRoleGranted("{\"roles\": [\"Provider\", \"Daher Clinician Admission\"]}"));
	}

	@Test
	public void receptionRoleOnly_shouldNotGrantProvider() throws Exception {
		Assert.assertFalse(isProviderRoleGranted("{\"roles\": [\"Daher Reception\"]}"));
	}

	@Test
	public void organizationalNurseRoleOnly_shouldNotGrantProvider() throws Exception {
		Assert.assertFalse(isProviderRoleGranted("{\"roles\": [\"Organizational: Nurse\"]}"));
	}

	@Test
	public void emptyRolesArray_shouldNotGrantProvider() throws Exception {
		Assert.assertFalse(isProviderRoleGranted("{\"roles\": []}"));
	}

	@Test
	public void missingRolesClaim_shouldFailClosed() throws Exception {
		Assert.assertFalse(isProviderRoleGranted("{\"sub\": \"someone\"}"));
	}

	@Test
	public void nullRolesClaim_shouldFailClosed() throws Exception {
		Assert.assertFalse(isProviderRoleGranted("{\"roles\": null}"));
	}

	@Test
	public void malformedRolesClaim_shouldFailClosed() throws Exception {
		// roles as a bare string instead of an array -- must not throw out of the login flow
		Assert.assertFalse(isProviderRoleGranted("{\"roles\": \"Provider\"}"));
	}

	@Test
	public void substringMatch_superProvider_shouldNotGrantProvider() throws Exception {
		Assert.assertFalse(isProviderRoleGranted("{\"roles\": [\"SuperProvider\"]}"));
	}

	@Test
	public void substringMatch_providerAssistant_shouldNotGrantProvider() throws Exception {
		Assert.assertFalse(isProviderRoleGranted("{\"roles\": [\"Provider Assistant\"]}"));
	}
}
