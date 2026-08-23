/*
 * This Source Code Form is subject to the terms of the Mozilla Public License,
 * v. 2.0. If a copy of the MPL was not distributed with this file, You can
 * obtain one at http://mozilla.org/MPL/2.0/. OpenMRS is also distributed under
 * the terms of the Healthcare Disclaimer located at http://openmrs.org/license.
 *
 * Copyright (C) OpenMRS Inc. OpenMRS is a registered trademark and the OpenMRS
 * graphic logo is a trademark of OpenMRS Inc.
 */
package org.openmrs.module.oauth2login.web.filter;

import java.io.IOException;

import javax.servlet.Filter;
import javax.servlet.FilterChain;
import javax.servlet.FilterConfig;
import javax.servlet.ServletException;
import javax.servlet.ServletRequest;
import javax.servlet.ServletResponse;

import org.openmrs.api.context.Context;

/**
 * DAHER PATCH (UI-007/DH-008B): bridges the module's own request-mapped {@code /oauth2login}
 * path to the one URL this OpenMRS's servlet mappings can actually route to a Spring MVC
 * controller ({@code /ws/oauth2login}, matched via the core {@code openmrs} servlet's static
 * {@code /ws/*} mapping), and wraps that forward in the module's own, unmodified
 * {@code oauth2ClientContextFilter} bean so that the
 * {@link org.springframework.security.oauth2.client.resource.UserRedirectRequiredException} it
 * throws is translated into a real browser redirect to the identity provider exactly the way
 * {@code webModuleApplicationContext.xml}'s Spring Security chain already implements it.
 * <p>
 * This filter does not construct authorization URLs, parse tokens, or otherwise duplicate any
 * OAuth2 logic — it only reuses the existing {@code oauth2ClientContextFilter} bean and OpenMRS's
 * own servlet dispatch. Registered in {@code config.xml} against the exact literal path
 * {@code /oauth2login} only, so it cannot affect any other request.
 */
public class OAuth2LoginBridgeFilter implements Filter {

	private static final String TARGET_PATH = "/ws/oauth2login";

	private static final String CLIENT_CONTEXT_FILTER_BEAN = "oauth2ClientContextFilter";

	@Override
	public void init(FilterConfig filterConfig) {
	}

	@Override
	public void destroy() {
	}

	@Override
	public void doFilter(ServletRequest request, ServletResponse response, FilterChain chain)
	        throws IOException, ServletException {

		Filter oauth2ClientContextFilter = Context.getRegisteredComponent(CLIENT_CONTEXT_FILTER_BEAN, Filter.class);

		oauth2ClientContextFilter.doFilter(request, response, (fwdRequest, fwdResponse) -> fwdRequest
		        .getRequestDispatcher(TARGET_PATH).forward(fwdRequest, fwdResponse));
	}
}
