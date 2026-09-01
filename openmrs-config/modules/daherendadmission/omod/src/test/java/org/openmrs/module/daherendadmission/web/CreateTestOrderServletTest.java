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
import static org.junit.Assert.fail;

import java.io.IOException;
import java.io.ByteArrayInputStream;
import java.nio.charset.StandardCharsets;

import org.junit.Test;
import org.openmrs.module.daherendadmission.DaherTestOrderOutcome;
import org.openmrs.module.daherendadmission.DaherTestOrderRequest;
import org.openmrs.module.daherendadmission.DaherTestOrderResult;

public class CreateTestOrderServletTest {

	private static final String ENCOUNTER = "11111111-1111-1111-1111-111111111111";

	private static final String CONCEPT = "22222222-2222-2222-2222-222222222222";

	private static final String CARE_SETTING = "33333333-3333-3333-3333-333333333333";

	private static final String VALID = "{\"encounterUuid\":\"" + ENCOUNTER + "\",\"conceptUuid\":\"" + CONCEPT
	        + "\",\"careSettingUuid\":\"" + CARE_SETTING + "\"}";

	@Test
	public void requestAcceptsExactlyTheThreeUuidFields() throws Exception {
		DaherTestOrderRequest request = CreateTestOrderServlet.parseRequest(VALID);
		assertEquals(ENCOUNTER, request.getEncounterUuid());
		assertEquals(CONCEPT, request.getConceptUuid());
		assertEquals(CARE_SETTING, request.getCareSettingUuid());
		assertTrue(CreateTestOrderServlet.isValid(request));
	}

	@Test
	public void endpointPathIsExactAndRequestBodyIsBounded() throws Exception {
		assertTrue(CreateTestOrderServlet.isExpectedPath("/daherendadmission/daherCreateTestOrder"));
		assertFalse(CreateTestOrderServlet.isExpectedPath("/daherCreateTestOrder"));
		assertFalse(CreateTestOrderServlet.isExpectedPath("/daherendadmission/daherCreateTestOrder/anything"));

		byte[] allowed = new byte[4096];
		assertEquals(4096,
		    CreateTestOrderServlet.readBody(new ByteArrayInputStream(allowed)).getBytes(StandardCharsets.UTF_8).length);
		try {
			CreateTestOrderServlet.readBody(new ByteArrayInputStream(new byte[4097]));
			fail("accepted request body larger than 4096 bytes");
		}
		catch (IOException expected) {
			// expected
		}
	}

	@Test
	public void requestRejectsEveryCallerControlledServerField() {
		for (String field : new String[] { "patientUuid", "ordererUuid", "orderTypeUuid", "orderNumber", "action",
		        "previousOrder" }) {
			try {
				CreateTestOrderServlet
				        .parseRequest(VALID.substring(0, VALID.length() - 1) + ",\"" + field + "\":\"attacker-value\"}");
				fail("accepted forbidden field " + field);
			}
			catch (IOException expected) {
				// expected
			}
		}
	}

	@Test
	public void requestRejectsMissingMalformedDuplicateAndTrailingInput() throws Exception {
		DaherTestOrderRequest missing = CreateTestOrderServlet.parseRequest("{\"encounterUuid\":\"" + ENCOUNTER + "\"}");
		assertFalse(CreateTestOrderServlet.isValid(missing));
		assertFalse(
		    CreateTestOrderServlet.isValid(CreateTestOrderServlet.parseRequest(VALID.replace(CARE_SETTING, "not-a-uuid"))));
		assertParseFails(VALID.substring(0, VALID.length() - 1) + ",\"conceptUuid\":\"" + CONCEPT + "\"}");
		assertParseFails(VALID + " {}");
	}

	@Test
	public void everyOutcomeHasAnExplicitNonGenericHttpMapping() {
		for (DaherTestOrderOutcome outcome : DaherTestOrderOutcome.values()) {
			int status = CreateTestOrderServlet.httpStatusFor(outcome);
			assertTrue(status == 201 || status == 400 || status == 403 || status == 404 || status == 500);
		}
		assertEquals(201, CreateTestOrderServlet.httpStatusFor(DaherTestOrderOutcome.CREATED));
		assertEquals(403, CreateTestOrderServlet.httpStatusFor(DaherTestOrderOutcome.AMBIGUOUS_PROVIDER));
	}

	@Test
	public void successResponseContainsOnlyStatusUuidAndNativeOrderNumber() {
		DaherTestOrderResult result = new DaherTestOrderResult(DaherTestOrderOutcome.CREATED,
		        "44444444-4444-4444-4444-444444444444", "ORD-123");
		String json = CreateTestOrderServlet.toResultJson(result);
		assertEquals(
		    "{\"status\":\"CREATED\",\"orderUuid\":\"44444444-4444-4444-4444-444444444444\",\"orderNumber\":\"ORD-123\"}",
		    json);
		assertEquals(3, json.split("\":").length - 1);
		assertFalse(json.contains("Get Orders"));
		assertFalse(json.contains("patient"));
		assertFalse(json.contains("orderer"));
	}

	private static void assertParseFails(String json) {
		try {
			CreateTestOrderServlet.parseRequest(json);
			fail("expected parse failure");
		}
		catch (IOException expected) {
			// expected
		}
	}
}
