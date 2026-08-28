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
 * Every possible result of {@link DaherMaternityAdmissionService#endAdmission}. Deliberately
 * exhaustive and closed -- the web layer must handle every value explicitly rather than falling
 * through to a generic error, so a new failure mode can never silently look like success.
 */
public enum EndAdmissionOutcome {
	ENDED,
	ALREADY_ENDED,
	VISIT_NOT_FOUND,
	WRONG_VISIT_TYPE,
	WRONG_LOCATION,
	UNAUTHORIZED
}
