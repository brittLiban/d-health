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

/** Minimal result built directly from the saved object; never generic Order REST serialization. */
public class DaherTestOrderResult {

	private final DaherTestOrderOutcome outcome;

	private final String orderUuid;

	private final String orderNumber;

	public DaherTestOrderResult(DaherTestOrderOutcome outcome, String orderUuid, String orderNumber) {
		this.outcome = outcome;
		this.orderUuid = orderUuid;
		this.orderNumber = orderNumber;
	}

	public DaherTestOrderOutcome getOutcome() {
		return outcome;
	}

	public String getOrderUuid() {
		return orderUuid;
	}

	public String getOrderNumber() {
		return orderNumber;
	}
}
