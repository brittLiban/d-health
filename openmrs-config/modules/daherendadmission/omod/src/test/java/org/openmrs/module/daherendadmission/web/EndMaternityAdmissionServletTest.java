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
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import java.util.Date;

import org.junit.Test;
import org.openmrs.module.daherendadmission.EndAdmissionOutcome;
import org.openmrs.module.daherendadmission.EndAdmissionResult;

/**
 * Plain JUnit unit tests, no OpenMRS Context, no BaseModuleContextSensitiveTest -- everything here
 * runs against pure, package-private static helpers extracted from the servlet specifically so they
 * are testable without standing up a full OpenMRS test context. Transaction, bed, queue, and
 * concurrency behaviour are covered by ADR-030's live proof against the real disposable stack
 * (DECISIONS.md/PROJECT_STATE.md), not by JUnit here -- a context-sensitive test with an H2
 * database would not have caught either of the two real bugs this module shipped (the getPathInfo()
 * prefix, and the Get Providers privilege gap), since neither reproduces without the real
 * module-servlet dispatcher or the real privilege graph. What IS covered here is exactly what a
 * pure unit test can meaningfully guard: the path-parsing regex (the regression guard for the first
 * bug) and the complete outcome-to-response mapping (so a future added EndAdmissionOutcome can
 * never silently fall through to an unmapped, wrong, or leaky response).
 */
public class EndMaternityAdmissionServletTest {

	private static final String VALID_UUID = "86f7066b-0caf-43d8-a65b-fbe77970d5bc";

	private static final String VALID_PATH_INFO = "/daherendadmission/daherEndMaternityAdmission/" + VALID_UUID + "/end";

	// --- extractVisitUuid / PATH_INFO_PATTERN --------------------------------------------------

	@Test
	public void extractVisitUuid_matchesTheRealModuleServletDispatcherShape() {
		assertEquals(VALID_UUID, EndMaternityAdmissionServlet.extractVisitUuid(VALID_PATH_INFO));
	}

	@Test
	public void extractVisitUuid_rejectsAPathRelativeToTheServletsOwnMapping() {
		// The exact shape the original (buggy) regex assumed -- the regression this test guards.
		assertNull(EndMaternityAdmissionServlet.extractVisitUuid("/" + VALID_UUID + "/end"));
	}

	@Test
	public void extractVisitUuid_rejectsAWrongModuleIdPrefix() {
		assertNull(
		    EndMaternityAdmissionServlet.extractVisitUuid("/wrongmodule/daherEndMaternityAdmission/" + VALID_UUID + "/end"));
	}

	@Test
	public void extractVisitUuid_rejectsAWrongServletNamePrefix() {
		assertNull(
		    EndMaternityAdmissionServlet.extractVisitUuid("/daherendadmission/wrongServletName/" + VALID_UUID + "/end"));
	}

	@Test
	public void extractVisitUuid_rejectsAMalformedUuid() {
		assertNull(EndMaternityAdmissionServlet
		        .extractVisitUuid("/daherendadmission/daherEndMaternityAdmission/not-a-real-uuid/end"));
	}

	@Test
	public void extractVisitUuid_rejectsATrailingSlashOrExtraSegment() {
		assertNull(EndMaternityAdmissionServlet.extractVisitUuid(VALID_PATH_INFO + "/"));
		assertNull(EndMaternityAdmissionServlet.extractVisitUuid(VALID_PATH_INFO + "/extra"));
	}

	@Test
	public void extractVisitUuid_rejectsEmptyString() {
		assertNull(EndMaternityAdmissionServlet.extractVisitUuid(""));
	}

	// --- httpStatusFor: the complete, closed outcome -> HTTP status mapping --------------------

	@Test
	public void httpStatusFor_everyOutcomeValueMapsToExactlyOneKnownStatus() {
		for (EndAdmissionOutcome outcome : EndAdmissionOutcome.values()) {
			int status = EndMaternityAdmissionServlet.httpStatusFor(outcome);
			assertTrue("unexpected HTTP status " + status + " for " + outcome,
			    status == 200 || status == 400 || status == 403 || status == 404 || status == 500);
		}
	}

	@Test
	public void httpStatusFor_endedAndAlreadyEndedAreBothSuccess() {
		assertEquals(200, EndMaternityAdmissionServlet.httpStatusFor(EndAdmissionOutcome.ENDED));
		assertEquals(200, EndMaternityAdmissionServlet.httpStatusFor(EndAdmissionOutcome.ALREADY_ENDED));
	}

	@Test
	public void httpStatusFor_unauthorizedIsForbiddenNotServerError() {
		// The specific case UI-015E's own bug was about: an authorization failure must be
		// distinguishable from a genuine operation failure, never conflated as 500.
		assertEquals(403, EndMaternityAdmissionServlet.httpStatusFor(EndAdmissionOutcome.UNAUTHORIZED));
	}

	@Test
	public void httpStatusFor_visitNotFoundIs404() {
		assertEquals(404, EndMaternityAdmissionServlet.httpStatusFor(EndAdmissionOutcome.VISIT_NOT_FOUND));
	}

	@Test
	public void httpStatusFor_wrongVisitTypeAndWrongLocationAreBothValidationErrors() {
		assertEquals(400, EndMaternityAdmissionServlet.httpStatusFor(EndAdmissionOutcome.WRONG_VISIT_TYPE));
		assertEquals(400, EndMaternityAdmissionServlet.httpStatusFor(EndAdmissionOutcome.WRONG_LOCATION));
	}

	// --- toStatusJson / toResultJson: response-body shape ---------------------------------------

	@Test
	public void toStatusJson_containsOnlyTheStatusField() {
		assertEquals("{\"status\":\"FORBIDDEN\"}", EndMaternityAdmissionServlet.toStatusJson("FORBIDDEN"));
	}

	@Test
	public void toStatusJson_neverLeaksAPrivilegeNameOrInternalUser() {
		for (String status : new String[] { "UNAUTHENTICATED", "MALFORMED_REQUEST", "VISIT_NOT_FOUND", "WRONG_VISIT_TYPE",
		        "WRONG_LOCATION", "FORBIDDEN", "OPERATION_FAILED" }) {
			String json = EndMaternityAdmissionServlet.toStatusJson(status);
			assertFalse(json, json.toLowerCase().contains("privilege"));
			assertFalse(json, json.toLowerCase().contains("daemon"));
			assertFalse(json, json.toLowerCase().contains("exception"));
			assertFalse(json, json.contains("org.openmrs"));
		}
	}

	@Test
	public void toResultJson_endedIncludesStatusVisitUuidAndStopDatetime() {
		Date now = new Date();
		EndAdmissionResult result = new EndAdmissionResult(EndAdmissionOutcome.ENDED, VALID_UUID, now);
		String json = EndMaternityAdmissionServlet.toResultJson(result);
		assertTrue(json, json.contains("\"status\":\"ENDED\""));
		assertTrue(json, json.contains("\"visitUuid\":\"" + VALID_UUID + "\""));
		assertTrue(json, json.contains("\"stopDatetime\":"));
	}

	@Test
	public void toResultJson_omitsStopDatetimeFieldEntirelyWhenNull() {
		EndAdmissionResult result = new EndAdmissionResult(EndAdmissionOutcome.VISIT_NOT_FOUND, VALID_UUID, null);
		String json = EndMaternityAdmissionServlet.toResultJson(result);
		assertFalse(json, json.contains("stopDatetime"));
	}

	@Test
	public void toResultJson_neverIncludesAnyFieldBeyondStatusVisitUuidAndStopDatetime() {
		// A response containing the initiating clinician's UUID, a daemon user, or a bed/queue
		// identifier would be exactly the internal-detail leakage this endpoint's own contract
		// forbids -- this asserts the JSON has no keys beyond the three documented ones.
		EndAdmissionResult result = new EndAdmissionResult(EndAdmissionOutcome.ENDED, VALID_UUID, new Date());
		String json = EndMaternityAdmissionServlet.toResultJson(result);
		int fieldCount = json.split("\":").length - 1;
		assertEquals(json, 3, fieldCount);
	}

	@Test
	public void toResultJson_escapesAVisitUuidContainingAQuoteOrBackslash() {
		// visitUuid is always a real UUID in production, never attacker-controlled JSON structure --
		// this is defense in depth for the escape() helper itself, not a reachable production input.
		EndAdmissionResult result = new EndAdmissionResult(EndAdmissionOutcome.ENDED, "a\"b\\c", new Date());
		String json = EndMaternityAdmissionServlet.toResultJson(result);
		assertTrue(json, json.contains("a\\\"b\\\\c"));
	}
}
