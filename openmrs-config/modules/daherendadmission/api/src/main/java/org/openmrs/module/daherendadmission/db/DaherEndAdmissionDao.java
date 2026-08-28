/*
 * This Source Code Form is subject to the terms of the Mozilla Public License,
 * v. 2.0. If a copy of the MPL was not distributed with this file, You can
 * obtain one at http://mozilla.org/MPL/2.0/. OpenMRS is also distributed under
 * the terms of the Healthcare Disclaimer located at http://openmrs.org/license.
 *
 * Copyright (C) OpenMRS Inc. OpenMRS is a registered trademark and the OpenMRS
 * graphic logo is a trademark of OpenMRS Inc.
 */
package org.openmrs.module.daherendadmission.db;

import org.openmrs.Visit;

public interface DaherEndAdmissionDao {

	/**
	 * Loads the Visit by UUID under a database row lock (PESSIMISTIC_WRITE), inside the caller's
	 * already-active transaction. A second concurrent caller for the same UUID blocks here until the
	 * first transaction commits or rolls back, then sees the first caller's committed state -- this is
	 * what makes two simultaneous end-admission calls resolve to exactly one logical close rather than
	 * a race. Returns null if no Visit has that UUID.
	 */
	Visit getVisitByUuidForUpdate(String uuid);
}
