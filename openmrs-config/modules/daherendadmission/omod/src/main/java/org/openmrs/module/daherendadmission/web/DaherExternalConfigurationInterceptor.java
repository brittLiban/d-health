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

import java.io.IOException;

import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

import org.openmrs.api.context.Context;
import org.openmrs.module.daherendadmission.DaherEndAdmissionConstants;
import org.springframework.web.servlet.handler.HandlerInterceptorAdapter;

/**
 * HTTP-only guard for pinned configuration controllers. It runs after REST authentication and never
 * changes OpenMRS service privileges, so internal patient search and order numbering retain the
 * core {@code Authenticated} compatibility contract.
 */
public class DaherExternalConfigurationInterceptor extends HandlerInterceptorAdapter {

	@Override
	public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler) throws IOException {
		if (!isAuthenticated()) {
			writeDenied(response, HttpServletResponse.SC_UNAUTHORIZED, "UNAUTHENTICATED");
			return false;
		}
		if (!hasExternalConfigurationPrivilege()) {
			writeDenied(response, HttpServletResponse.SC_FORBIDDEN, "FORBIDDEN");
			return false;
		}
		return true;
	}

	protected boolean isAuthenticated() {
		return Context.isAuthenticated();
	}

	protected boolean hasExternalConfigurationPrivilege() {
		return Context.hasPrivilege(DaherEndAdmissionConstants.VIEW_SYSTEM_CONFIGURATION_PRIVILEGE);
	}

	private static void writeDenied(HttpServletResponse response, int status, String publicStatus) throws IOException {
		response.setStatus(status);
		response.setContentType("application/json");
		response.getWriter().write("{\"status\":\"" + publicStatus + "\"}");
	}
}
