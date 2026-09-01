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

import java.util.List;

import org.openmrs.module.initializer.Domain;
import org.openmrs.module.initializer.api.loaders.BaseLoader;

/**
 * Adds the external configuration privilege only after Initializer has imported roles.
 * <p>
 * OpenMRS does not order module {@code started()} callbacks by declared dependencies. Registering
 * this as an Initializer loader provides the required deterministic point after the roles domain,
 * while the provisioner itself remains idempotent and fail-closed.
 * </p>
 */
public class DaherExternalConfigurationPrivilegeLoader extends BaseLoader {

	@Override
	protected Domain getDomain() {
		return Domain.ROLES;
	}

	@Override
	public Integer getOrder() {
		return Domain.ROLES.getOrder() + 1;
	}

	@Override
	public void loadUnsafe(List<String> wildcardExclusions, boolean failOnError) {
		DaherExternalConfigurationPrivilegeProvisioner.ensureSysAdminGrant();
	}
}
