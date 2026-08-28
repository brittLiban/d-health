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

public interface DaherMaternityAdmissionService {

	/**
	 * Ends the canonical Daher Maternity Admission Visit identified by visitUuid, if it is eligible, by
	 * setting its stopDatetime. Accepts no other input -- the server determines the stop time itself
	 * and re-validates visit type/location/state authoritatively rather than trusting anything about
	 * the caller's assumptions. Must be called as the OUTERMOST entry point on the calling thread (i.e.
	 * this method itself, not visitService.saveVisit() directly) so that this Spring proxy's own
	 * transaction is already active before any nested OpenMRS/bedmanagement/queue service call runs --
	 * see the TransactionProxyFactoryBean wiring in moduleApplicationContext.xml and ADR-030's
	 * transaction-map analysis for why that ordering is load-bearing. Also performs the full
	 * authorization check (Daher Clinician Admission role + an actual Provider record) itself, here,
	 * under daemon elevation -- not in the caller's un-elevated request thread. getProvidersByPerson
	 * requires the Get Providers privilege, which the canonical role deliberately does not grant
	 * (ADR-030 forbids granting new broad privileges), so this check can only run inside the
	 * daemon-elevated call. initiatingUserUuid is trusted as the real caller's identity only because
	 * the servlet captures it from Context.getAuthenticatedUser() BEFORE elevating -- this method must
	 * never substitute Context.getAuthenticatedUser() for it, since inside a daemon thread that
	 * resolves to the daemon user, not the caller.
	 *
	 * @param visitUuid the canonical Visit's UUID -- the only input this operation accepts
	 * @param initiatingUserUuid the real, authenticated clinician's OpenMRS User UUID, captured by the
	 *            caller BEFORE any daemon-context elevation, for authorization and for the technical
	 *            audit record
	 * @return the outcome; never throws for an expected negative case (unauthorized / not found / wrong
	 *         type / wrong location / already ended) -- only for genuine unexpected failures, which
	 *         roll back the whole operation
	 */
	EndAdmissionResult endAdmission(String visitUuid, String initiatingUserUuid);
}
