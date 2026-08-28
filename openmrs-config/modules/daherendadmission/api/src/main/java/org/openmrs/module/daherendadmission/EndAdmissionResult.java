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

import java.util.Date;

/**
 * The narrow, safe-to-serialize result of an end-admission attempt. Carries nothing internal: no
 * daemon user identity, no privilege names, no bed/queue details, no stack traces.
 */
public class EndAdmissionResult {

	private final EndAdmissionOutcome outcome;

	private final String visitUuid;

	private final Date stopDatetime;

	public EndAdmissionResult(EndAdmissionOutcome outcome, String visitUuid, Date stopDatetime) {
		this.outcome = outcome;
		this.visitUuid = visitUuid;
		this.stopDatetime = stopDatetime;
	}

	public EndAdmissionOutcome getOutcome() {
		return outcome;
	}

	public String getVisitUuid() {
		return visitUuid;
	}

	public Date getStopDatetime() {
		return stopDatetime;
	}
}
