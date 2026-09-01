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

/** The complete request contract. No patient, orderer, order type, or order number is accepted. */
public class DaherTestOrderRequest {

	private String encounterUuid;

	private String conceptUuid;

	private String careSettingUuid;

	public String getEncounterUuid() {
		return encounterUuid;
	}

	public void setEncounterUuid(String encounterUuid) {
		this.encounterUuid = encounterUuid;
	}

	public String getConceptUuid() {
		return conceptUuid;
	}

	public void setConceptUuid(String conceptUuid) {
		this.conceptUuid = conceptUuid;
	}

	public String getCareSettingUuid() {
		return careSettingUuid;
	}

	public void setCareSettingUuid(String careSettingUuid) {
		this.careSettingUuid = careSettingUuid;
	}
}
