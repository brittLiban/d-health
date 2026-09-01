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

import org.openmrs.api.context.Context;

/** Executes one internal operation with one temporary OpenMRS proxy privilege. */
public class DaherProxyPrivilegeExecutor {

	@FunctionalInterface
	public interface Operation<T> {

		T execute();
	}

	public <T> T execute(String privilege, Operation<T> operation) {
		add(privilege);
		try {
			return operation.execute();
		}
		finally {
			remove(privilege);
		}
	}

	protected void add(String privilege) {
		Context.addProxyPrivilege(privilege);
	}

	protected void remove(String privilege) {
		Context.removeProxyPrivilege(privilege);
	}
}
