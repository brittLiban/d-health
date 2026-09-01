/*
 * This Source Code Form is subject to the terms of the Mozilla Public License,
 * v. 2.0. If a copy of the MPL was not distributed with this file, You can
 * obtain one at http://mozilla.org/MPL/2.0/. OpenMRS is also distributed under
 * the terms of the Healthcare Disclaimer located at http://openmrs.org/license.
 *
 * Copyright (C) OpenMRS Inc. OpenMRS is a registered trademark and the OpenMRS
 * graphic logo is a trademark of OpenMRS Inc.
 */
package org.openmrs.module.daherendadmission.web;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import org.junit.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;

public class DaherExternalConfigurationInterceptorTest {

	@Test
	public void unauthenticatedRequestIsDeniedWithoutCallingHandler() throws Exception {
		TestInterceptor interceptor = new TestInterceptor(false, false);
		MockHttpServletResponse response = new MockHttpServletResponse();

		assertFalse(interceptor.preHandle(new MockHttpServletRequest(), response, new Object()));
		assertEquals(401, response.getStatus());
		assertEquals("{\"status\":\"UNAUTHENTICATED\"}", response.getContentAsString());
	}

	@Test
	public void ordinaryAuthenticatedRequestIsDeniedWithoutConfigurationContent() throws Exception {
		TestInterceptor interceptor = new TestInterceptor(true, false);
		MockHttpServletResponse response = new MockHttpServletResponse();

		assertFalse(interceptor.preHandle(new MockHttpServletRequest(), response, new Object()));
		assertEquals(403, response.getStatus());
		assertEquals("{\"status\":\"FORBIDDEN\"}", response.getContentAsString());
		assertFalse(response.getContentAsString().contains("value"));
	}

	@Test
	public void sysAdminPrivilegePassesToUnmodifiedUpstreamHandler() throws Exception {
		TestInterceptor interceptor = new TestInterceptor(true, true);
		MockHttpServletResponse response = new MockHttpServletResponse();

		assertTrue(interceptor.preHandle(new MockHttpServletRequest(), response, new Object()));
		assertEquals(200, response.getStatus());
		assertEquals("", response.getContentAsString());
	}

	private static class TestInterceptor extends DaherExternalConfigurationInterceptor {

		private final boolean authenticated;

		private final boolean privileged;

		TestInterceptor(boolean authenticated, boolean privileged) {
			this.authenticated = authenticated;
			this.privileged = privileged;
		}

		@Override
		protected boolean isAuthenticated() {
			return authenticated;
		}

		@Override
		protected boolean hasExternalConfigurationPrivilege() {
			return privileged;
		}
	}
}
