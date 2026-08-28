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

/**
 * Canonical UUIDs pinned by ADR-028/ADR-029 (Visit type, Location) and ADR-030 (audit
 * VisitAttributeType, provisioned via
 * openmrs-config/initializer/attributetypes/attributetypes-daher.csv). Never accept these as
 * request input -- the server always uses its own copy.
 */
public final class DaherEndAdmissionConstants {

	public static final String MATERNITY_ADMISSION_VISIT_TYPE_UUID = "194bf546-68d7-42c0-8371-5b0f2b1cf5f3";

	public static final String MATERNITE_LOCATION_UUID = "6538f097-382d-49b6-a839-601d03c888c0";

	public static final String AUDIT_VISIT_ATTRIBUTE_TYPE_UUID = "a66602e1-a519-4bea-a460-f4993fec5afb";

	public static final String CLINICIAN_ADMISSION_ROLE = "Daher Clinician Admission";

	private DaherEndAdmissionConstants() {
	}
}
