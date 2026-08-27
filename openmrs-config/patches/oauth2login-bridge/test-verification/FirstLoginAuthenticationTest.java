/*
 * This Source Code Form is subject to the terms of the Mozilla Public License,
 * v. 2.0. If a copy of the MPL was not distributed with this file, You can
 * obtain one at http://mozilla.org/MPL/2.0/. OpenMRS is also distributed under
 * the terms of the Healthcare Disclaimer located at http://openmrs.org/license.
 *
 * Copyright (C) OpenMRS Inc. OpenMRS is a registered trademark and the OpenMRS
 * graphic logo is a trademark of OpenMRS Inc.
 */
package org.openmrs.module.oauth2login.authscheme;

import java.lang.reflect.InvocationHandler;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.util.Arrays;
import java.util.List;
import java.util.Properties;

import org.junit.Assert;
import org.junit.Test;
import org.openmrs.User;
import org.openmrs.api.context.Authenticated;
import org.openmrs.api.context.ContextAuthenticationException;
import org.openmrs.api.db.ContextDAO;

/**
 * DAHER PATCH (Bug #25): regression coverage for the exact first-login authentication method.
 *
 * <p>The fake DAO deliberately keeps {@code getUserByUsername()} blind after creation, matching
 * the outer REPEATABLE_READ snapshot observed in the disposable stack. It separately returns the
 * authoritative User from {@code createUser()}, matching OpenMRS 2.8.7's daemon-creation contract.
 * No Mockito or Spring context is used, so this test runs on the pinned JDK 17 toolchain despite
 * the upstream test suite's pre-existing Mockito 1/CGLIB incompatibility.</p>
 */
public class FirstLoginAuthenticationTest {

	@Test
	public void firstLogin_shouldAuthenticateWithTheUserReturnedByCreateUser() throws Exception {
		User committedUser = new User();
		committedUser.setUserId(123);
		committedUser.setUsername("new-clinician");
		committedUser.setSystemId("keycloak-subject");
		RecordingContextDAO recording = new RecordingContextDAO(committedUser, null);
		OAuth2UserInfoAuthenticationScheme scheme = new TestableAuthenticationScheme(recording.proxy());

		Authenticated authenticated = scheme.authenticate(new OAuth2TokenCredentials(userInfo()));

		Assert.assertSame(committedUser, authenticated.getUser());
		Assert.assertEquals(1, recording.usernameLookups);
		Assert.assertEquals(1, recording.createCalls);
		Assert.assertEquals(Arrays.asList("Provider", "Daher Clinician Admission"), recording.createdRoles);
		Assert.assertEquals("new-clinician", recording.userPassedToCreate.getUsername());
		Assert.assertEquals("keycloak-subject", recording.userPassedToCreate.getSystemId());
	}

	@Test
	public void firstLogin_shouldNotRereadTheOldTransactionSnapshotAfterCreateUser() throws Exception {
		User committedUser = new User();
		committedUser.setUsername("new-clinician");
		RecordingContextDAO recording = new RecordingContextDAO(committedUser, null);
		OAuth2UserInfoAuthenticationScheme scheme = new TestableAuthenticationScheme(recording.proxy());

		scheme.authenticate(new OAuth2TokenCredentials(userInfo()));

		Assert.assertEquals("only the pre-create existence check is valid", 1, recording.usernameLookups);
	}

	@Test(expected = ContextAuthenticationException.class)
	public void firstLogin_shouldPropagateDaemonCreationFailure() {
		RecordingContextDAO recording = new RecordingContextDAO(null, new Exception("synthetic create failure"));
		OAuth2UserInfoAuthenticationScheme scheme = new TestableAuthenticationScheme(recording.proxy());

		scheme.authenticate(new OAuth2TokenCredentials(userInfo()));
	}

	private static UserInfo userInfo() {
		Properties mappings = new Properties();
		mappings.setProperty(UserInfo.PROP_USERNAME, "preferred_username");
		mappings.setProperty(UserInfo.PROP_SYSTEMID, "sub");
		mappings.setProperty(UserInfo.PROP_EMAIL, "email");
		mappings.setProperty(UserInfo.PROP_GIVEN_NAME, "given_name");
		mappings.setProperty(UserInfo.PROP_FAMILY_NAME, "family_name");
		mappings.setProperty(UserInfo.PROP_ROLES, "roles");
		return new UserInfo(mappings,
		        "{\"preferred_username\":\"new-clinician\",\"sub\":\"keycloak-subject\"," +
		                "\"email\":\"new-clinician@example.com\",\"given_name\":\"New\"," +
		                "\"family_name\":\"Clinician\",\"roles\":[\"Provider\"," +
		                "\"Daher Clinician Admission\"]}");
	}

	private static final class TestableAuthenticationScheme extends OAuth2UserInfoAuthenticationScheme {

		private final ContextDAO contextDAO;

		private TestableAuthenticationScheme(ContextDAO contextDAO) {
			this.contextDAO = contextDAO;
		}

		@Override
		protected ContextDAO getContextDAO() {
			return contextDAO;
		}
	}

	private static final class RecordingContextDAO implements InvocationHandler {

		private final User userReturnedByCreate;

		private final Exception createFailure;

		private int usernameLookups;

		private int createCalls;

		private User userPassedToCreate;

		private List<String> createdRoles;

		private RecordingContextDAO(User userReturnedByCreate, Exception createFailure) {
			this.userReturnedByCreate = userReturnedByCreate;
			this.createFailure = createFailure;
		}

		private ContextDAO proxy() {
			return (ContextDAO) Proxy.newProxyInstance(ContextDAO.class.getClassLoader(),
			    new Class<?>[] { ContextDAO.class }, this);
		}

		@Override
		@SuppressWarnings("unchecked")
		public Object invoke(Object proxy, Method method, Object[] args) throws Throwable {
			if ("getUserByUsername".equals(method.getName())) {
				usernameLookups++;
				return null;
			}
			if ("createUser".equals(method.getName())) {
				createCalls++;
				userPassedToCreate = (User) args[0];
				createdRoles = (List<String>) args[2];
				if (createFailure != null) {
					throw createFailure;
				}
				return userReturnedByCreate;
			}
			if ("toString".equals(method.getName())) {
				return "RecordingContextDAO";
			}
			if ("hashCode".equals(method.getName())) {
				return System.identityHashCode(proxy);
			}
			if ("equals".equals(method.getName())) {
				return proxy == args[0];
			}
			throw new AssertionError("unexpected ContextDAO method: " + method.getName());
		}
	}
}
