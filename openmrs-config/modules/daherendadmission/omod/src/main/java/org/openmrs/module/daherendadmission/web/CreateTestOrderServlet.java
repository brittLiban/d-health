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

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.HashSet;
import java.util.Set;
import java.util.regex.Pattern;

import javax.servlet.ServletException;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

import org.codehaus.jackson.JsonFactory;
import org.codehaus.jackson.JsonParser;
import org.codehaus.jackson.JsonToken;
import org.openmrs.User;
import org.openmrs.api.APIAuthenticationException;
import org.openmrs.api.ValidationException;
import org.openmrs.api.context.Context;
import org.openmrs.module.daherendadmission.DaherTestOrderOutcome;
import org.openmrs.module.daherendadmission.DaherTestOrderRequest;
import org.openmrs.module.daherendadmission.DaherTestOrderResult;
import org.openmrs.module.daherendadmission.DaherTestOrderService;
import org.openmrs.util.PrivilegeConstants;

import static org.openmrs.module.daherendadmission.DaherEndAdmissionConstants.CLINICIAN_ADMISSION_ROLE;

/**
 * Narrow V1 TestOrder CREATE boundary. It accepts exactly encounter, concept, and care-setting
 * UUIDs; patient and orderer are derived, Test Order type is fixed, and order number is generated
 * by OpenMRS. Unknown fields are rejected, so generic Order properties cannot be smuggled through.
 */
public class CreateTestOrderServlet extends HttpServlet {

	private static final long serialVersionUID = 1L;

	private static final int MAX_REQUEST_BYTES = 4096;

	private static final String PATH_INFO = "/daherendadmission/daherCreateTestOrder";

	private static final Pattern UUID = Pattern
	        .compile("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$");

	private static final JsonFactory JSON_FACTORY = new JsonFactory();

	@Override
	protected void doPost(HttpServletRequest req, HttpServletResponse resp) throws ServletException, IOException {
		resp.setContentType("application/json");

		if (!Context.isAuthenticated()) {
			writeStatus(resp, 401, "UNAUTHENTICATED");
			return;
		}
		User caller = Context.getAuthenticatedUser();
		if (caller == null || !caller.hasRole(CLINICIAN_ADMISSION_ROLE)
		        || !Context.hasPrivilege(PrivilegeConstants.ADD_ORDERS)) {
			writeStatus(resp, 403, "FORBIDDEN");
			return;
		}
		if (!isExpectedPath(req.getPathInfo())) {
			writeStatus(resp, 404, "NOT_FOUND");
			return;
		}
		String contentType = req.getContentType();
		int contentLength = req.getContentLength();
		if (contentType == null || !contentType.toLowerCase(java.util.Locale.ROOT).startsWith("application/json")
		        || contentLength > MAX_REQUEST_BYTES) {
			writeStatus(resp, contentLength > MAX_REQUEST_BYTES ? 413 : 400, "MALFORMED_REQUEST");
			return;
		}

		DaherTestOrderRequest request;
		try {
			request = parseRequest(readBody(req.getInputStream()));
		}
		catch (RequestTooLargeException e) {
			writeStatus(resp, 413, "MALFORMED_REQUEST");
			return;
		}
		catch (IOException | RuntimeException e) {
			writeStatus(resp, 400, "MALFORMED_REQUEST");
			return;
		}
		if (!isValid(request)) {
			writeStatus(resp, 400, "MALFORMED_REQUEST");
			return;
		}

		DaherTestOrderResult result;
		try {
			DaherTestOrderService service = Context.getRegisteredComponent("daherTestOrderService",
			    DaherTestOrderService.class);
			result = service.create(request);
		}
		catch (APIAuthenticationException e) {
			writeStatus(resp, 403, "FORBIDDEN");
			return;
		}
		catch (ValidationException e) {
			writeStatus(resp, 400, "INVALID_ORDER");
			return;
		}
		catch (RuntimeException e) {
			writeStatus(resp, 500, "OPERATION_FAILED");
			return;
		}

		int httpStatus = httpStatusFor(result.getOutcome());
		if (result.getOutcome() == DaherTestOrderOutcome.CREATED) {
			resp.setStatus(httpStatus);
			resp.getWriter().write(toResultJson(result));
		} else {
			writeStatus(resp, httpStatus, publicStatusFor(result.getOutcome()));
		}
	}

	static DaherTestOrderRequest parseRequest(String json) throws IOException {
		JsonParser parser = JSON_FACTORY.createJsonParser(json);
		try {
			if (parser.nextToken() != JsonToken.START_OBJECT) {
				throw new IOException("request must be one JSON object");
			}
			DaherTestOrderRequest request = new DaherTestOrderRequest();
			Set<String> seen = new HashSet<String>();
			while (parser.nextToken() != JsonToken.END_OBJECT) {
				if (parser.getCurrentToken() != JsonToken.FIELD_NAME) {
					throw new IOException("expected field name");
				}
				String field = parser.getCurrentName();
				if (!seen.add(field) || parser.nextToken() != JsonToken.VALUE_STRING) {
					throw new IOException("duplicate field or non-string value");
				}
				if ("encounterUuid".equals(field)) {
					request.setEncounterUuid(parser.getText());
				} else if ("conceptUuid".equals(field)) {
					request.setConceptUuid(parser.getText());
				} else if ("careSettingUuid".equals(field)) {
					request.setCareSettingUuid(parser.getText());
				} else {
					throw new IOException("unknown field");
				}
			}
			if (parser.nextToken() != null) {
				throw new IOException("trailing JSON token");
			}
			return request;
		}
		finally {
			parser.close();
		}
	}

	static boolean isExpectedPath(String pathInfo) {
		return PATH_INFO.equals(pathInfo);
	}

	static String readBody(InputStream input) throws IOException {
		ByteArrayOutputStream body = new ByteArrayOutputStream();
		byte[] buffer = new byte[512];
		int total = 0;
		int count;
		while ((count = input.read(buffer)) != -1) {
			total += count;
			if (total > MAX_REQUEST_BYTES) {
				throw new RequestTooLargeException();
			}
			body.write(buffer, 0, count);
		}
		return new String(body.toByteArray(), StandardCharsets.UTF_8);
	}

	static boolean isValid(DaherTestOrderRequest request) {
		return request != null && isUuid(request.getEncounterUuid()) && isUuid(request.getConceptUuid())
		        && isUuid(request.getCareSettingUuid());
	}

	private static boolean isUuid(String value) {
		return value != null && UUID.matcher(value).matches();
	}

	static int httpStatusFor(DaherTestOrderOutcome outcome) {
		switch (outcome) {
			case CREATED:
				return 201;
			case UNAUTHORIZED:
			case PROVIDER_NOT_FOUND:
			case AMBIGUOUS_PROVIDER:
				return 403;
			case ENCOUNTER_NOT_FOUND:
			case CONCEPT_NOT_FOUND:
			case CARE_SETTING_NOT_FOUND:
				return 404;
			case INELIGIBLE_ENCOUNTER:
				return 400;
			case TEST_ORDER_TYPE_UNAVAILABLE:
			default:
				return 500;
		}
	}

	static String publicStatusFor(DaherTestOrderOutcome outcome) {
		switch (outcome) {
			case UNAUTHORIZED:
			case PROVIDER_NOT_FOUND:
			case AMBIGUOUS_PROVIDER:
				return "FORBIDDEN";
			case ENCOUNTER_NOT_FOUND:
				return "ENCOUNTER_NOT_FOUND";
			case CONCEPT_NOT_FOUND:
				return "CONCEPT_NOT_FOUND";
			case CARE_SETTING_NOT_FOUND:
				return "CARE_SETTING_NOT_FOUND";
			case INELIGIBLE_ENCOUNTER:
				return "INELIGIBLE_ENCOUNTER";
			default:
				return "OPERATION_FAILED";
		}
	}

	private static void writeStatus(HttpServletResponse resp, int httpStatus, String status) throws IOException {
		resp.setStatus(httpStatus);
		resp.getWriter().write(toStatusJson(status));
	}

	static String toStatusJson(String status) {
		return "{\"status\":\"" + escape(status) + "\"}";
	}

	static String toResultJson(DaherTestOrderResult result) {
		return "{\"status\":\"CREATED\",\"orderUuid\":\"" + escape(result.getOrderUuid()) + "\",\"orderNumber\":\""
		        + escape(result.getOrderNumber()) + "\"}";
	}

	private static String escape(String value) {
		return value == null ? "" : value.replace("\\", "\\\\").replace("\"", "\\\"");
	}

	private static final class RequestTooLargeException extends IOException {

		private static final long serialVersionUID = 1L;
	}
}
