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
import java.text.SimpleDateFormat;
import java.util.Locale;
import java.util.TimeZone;
import java.util.concurrent.atomic.AtomicReference;
import java.util.regex.Pattern;

import javax.servlet.ServletException;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

import org.openmrs.User;
import org.openmrs.api.context.Context;
import org.openmrs.api.context.Daemon;
import org.openmrs.module.daherendadmission.DaherEndAdmissionDaemon;
import org.openmrs.module.daherendadmission.DaherMaternityAdmissionService;
import org.openmrs.module.daherendadmission.EndAdmissionOutcome;
import org.openmrs.module.daherendadmission.EndAdmissionResult;

/**
 * The narrow, single-purpose HTTP entry point for ADR-030's end-admission operation. Reachable at
 * POST /openmrs/moduleServlet/daherendadmission/daherEndMaternityAdmission/{visitUuid}/end Accepts
 * no request body and no fields other than the Visit UUID embedded in the URL path -- no
 * stopDatetime, startDatetime, patient, location, visitType, bed, or queue field is ever read from
 * the request. The server determines the stop time itself and re-validates everything else
 * authoritatively (DaherMaternityAdmissionServiceImpl). A raw HttpServlet, not a Spring
 * MVC @Controller, is used deliberately: this exact mechanism was already live-proven end-to-end
 * across UI-015/UI-015D's spikes, and this class's own <servlet-name>
 * ("daherEndMaternityAdmission", see config.xml) is chosen to be globally unique -- UI-015D found
 * that two OpenMRS modules sharing a <servlet-name> breaks Spring context refresh for the entire
 * instance, not just the colliding modules.
 */
public class EndMaternityAdmissionServlet extends HttpServlet {

	private static final long serialVersionUID = 1L;

	/**
	 * OpenMRS's module-servlet dispatcher (the single Tomcat servlet mapped at /moduleServlet/*)
	 * resolves moduleId/servletName itself and invokes this servlet's service() method directly -- it
	 * does not forward/include in a way that rewrites getPathInfo() to be relative to THIS servlet's
	 * own mapping. So req.getPathInfo() here is still prefixed with /{moduleId}/{servletName}, exactly
	 * as seen on the original request. Must be kept in sync with pom.xml's artifactId
	 * ("daherendadmission") and config.xml's <servlet-name> ("daherEndMaternityAdmission"). Found by
	 * live-proof evidence, not assumption -- see ADR-030.
	 */
	private static final Pattern PATH_INFO_PATTERN = Pattern
	        .compile("^/daherendadmission/daherEndMaternityAdmission/([0-9a-fA-F-]{36})/end$");

	@Override
	protected void doPost(HttpServletRequest req, HttpServletResponse resp) throws ServletException, IOException {
		resp.setContentType("application/json");

		if (!Context.isAuthenticated()) {
			writeStatus(resp, 401, "UNAUTHENTICATED");
			return;
		}

		// Authorization (Daher Clinician Admission role + an actual Provider record) is deliberately
		// NOT checked here. getProvidersByPerson requires the Get Providers privilege, which the
		// canonical role does not grant (ADR-030 forbids granting new broad privileges), so that
		// check can only run under daemon elevation -- see
		// DaherMaternityAdmissionServiceImpl.isAuthorizedInitiator, called inside the Runnable below.
		User user = Context.getAuthenticatedUser();

		String pathInfo = req.getPathInfo();
		String visitUuid = pathInfo == null ? null : extractVisitUuid(pathInfo);
		if (visitUuid == null) {
			writeStatus(resp, 400, "MALFORMED_REQUEST");
			return;
		}

		String initiatingUserUuid = user.getUuid();
		AtomicReference<EndAdmissionResult> resultRef = new AtomicReference<>();
		AtomicReference<RuntimeException> failureRef = new AtomicReference<>();

		Runnable work = () -> {
			try {
				DaherMaternityAdmissionService svc = Context.getRegisteredComponent("daherMaternityAdmissionService",
				    DaherMaternityAdmissionService.class);
				resultRef.set(svc.endAdmission(visitUuid, initiatingUserUuid));
			}
			catch (RuntimeException e) {
				failureRef.set(e);
			}
		};

		Daemon.runInDaemonThreadAndWait(work, DaherEndAdmissionDaemon.getToken());

		if (failureRef.get() != null) {
			// Deliberately no exception detail in the response -- see class javadoc. The
			// transaction this method's own service proxy opened has already rolled back
			// everything (Visit, bed, queue, audit record) by the time control reaches here.
			writeStatus(resp, 500, "OPERATION_FAILED");
			return;
		}

		EndAdmissionResult result = resultRef.get();
		EndAdmissionOutcome outcome = result.getOutcome();
		int httpStatus = httpStatusFor(outcome);
		switch (outcome) {
			case ENDED:
			case ALREADY_ENDED:
				resp.setStatus(httpStatus);
				resp.getWriter().write(toResultJson(result));
				return;
			case VISIT_NOT_FOUND:
				writeStatus(resp, httpStatus, "VISIT_NOT_FOUND");
				return;
			case WRONG_VISIT_TYPE:
			case WRONG_LOCATION:
				writeStatus(resp, httpStatus, outcome.name());
				return;
			case UNAUTHORIZED:
				writeStatus(resp, httpStatus, "FORBIDDEN");
				return;
			default:
				// Deliberately no exception detail -- see class javadoc.
				writeStatus(resp, httpStatus, "OPERATION_FAILED");
		}
	}

	/**
	 * Package-private (not private) so EndMaternityAdmissionServletTest can call it directly without
	 * reflection -- the highest-value regression guard in this class: it is what catches a future
	 * servlet-name/module-id rename in config.xml or pom.xml that isn't mirrored here (exactly the bug
	 * this pattern itself was written to fix, see the javadoc on PATH_INFO_PATTERN above).
	 */
	static String extractVisitUuid(String pathInfo) {
		java.util.regex.Matcher m = PATH_INFO_PATTERN.matcher(pathInfo);
		return m.matches() ? m.group(1) : null;
	}

	/**
	 * The complete, closed outcome -> HTTP status mapping, package-private and pure (no I/O) so it is
	 * directly unit-testable against every {@link EndAdmissionOutcome} value, including one a future
	 * change might add without updating this method -- see EndMaternityAdmissionServletTest's
	 * exhaustive-mapping test.
	 */
	static int httpStatusFor(EndAdmissionOutcome outcome) {
		switch (outcome) {
			case ENDED:
			case ALREADY_ENDED:
				return 200;
			case VISIT_NOT_FOUND:
				return 404;
			case WRONG_VISIT_TYPE:
			case WRONG_LOCATION:
				return 400;
			case UNAUTHORIZED:
				return 403;
			default:
				return 500;
		}
	}

	private void writeStatus(HttpServletResponse resp, int httpStatus, String status) throws IOException {
		resp.setStatus(httpStatus);
		resp.getWriter().write(toStatusJson(status));
	}

	/**
	 * Pure (no I/O), package-private JSON body builder for the {status} response shape -- directly
	 * unit-testable for exactly what EndMaternityAdmissionServletTest's response-shape tests check:
	 * never a privilege name, internal user, exception message, or stack trace, regardless of outcome.
	 */
	static String toStatusJson(String status) {
		return "{\"status\":\"" + status + "\"}";
	}

	/**
	 * Pure (no I/O), package-private JSON body builder for the {status, visitUuid, stopDatetime}
	 * success-response shape. Never includes anything beyond those three fields -- no daemon user, no
	 * initiating clinician's own UUID, no privilege name.
	 */
	static String toResultJson(EndAdmissionResult result) {
		SimpleDateFormat iso = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSSZ", Locale.US);
		iso.setTimeZone(TimeZone.getTimeZone("UTC"));
		String stopDatetime = result.getStopDatetime() == null ? null : iso.format(result.getStopDatetime());
		StringBuilder json = new StringBuilder();
		json.append("{\"status\":\"").append(result.getOutcome().name()).append("\"");
		json.append(",\"visitUuid\":\"").append(escape(result.getVisitUuid())).append("\"");
		if (stopDatetime != null) {
			json.append(",\"stopDatetime\":\"").append(stopDatetime).append("\"");
		}
		json.append("}");
		return json.toString();
	}

	private static String escape(String s) {
		return s == null ? "" : s.replace("\\", "\\\\").replace("\"", "\\\"");
	}
}
