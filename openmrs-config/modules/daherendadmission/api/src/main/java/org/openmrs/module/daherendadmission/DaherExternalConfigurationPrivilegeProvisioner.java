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

import org.openmrs.Privilege;
import org.openmrs.Role;
import org.openmrs.api.UserService;
import org.openmrs.api.context.Context;
import org.openmrs.util.PrivilegeConstants;
import org.openmrs.util.RoleConstants;

/** Provisions ADR-041's HTTP-only configuration visibility privilege to the sys-admin role. */
public final class DaherExternalConfigurationPrivilegeProvisioner {

	private DaherExternalConfigurationPrivilegeProvisioner() {
	}

	public static void ensureSysAdminGrant() {
		Context.addProxyPrivilege(PrivilegeConstants.GET_ROLES);
		Context.addProxyPrivilege(PrivilegeConstants.GET_PRIVILEGES);
		Context.addProxyPrivilege(PrivilegeConstants.MANAGE_ROLES);
		try {
			UserService userService = Context.getUserService();
			Role authenticated = userService.getRole(RoleConstants.AUTHENTICATED);
			Role sysAdmin = userService.getRole(DaherEndAdmissionConstants.SYS_ADMIN_ROLE);
			Privilege externalRead = userService
			        .getPrivilege(DaherEndAdmissionConstants.VIEW_SYSTEM_CONFIGURATION_PRIVILEGE);

			if (authenticated == null || sysAdmin == null || externalRead == null) {
				throw new IllegalStateException(
				        "Required roles/privilege are missing; refusing partial external configuration hardening");
			}
			if (authenticated.getPrivileges().contains(externalRead)) {
				throw new IllegalStateException("Authenticated must not hold the Daher external configuration privilege");
			}
			// Initializer's upstream wildcard roles absorb every privilege already present when the roles
			// domain is reloaded. That includes this module-declared privilege on a second boot. Enforce
			// ADR-041's direct-holder allowlist after the roles loader on every boot instead of relying on
			// first-boot timing.
			for (Role role : userService.getAllRoles()) {
				if (!DaherEndAdmissionConstants.SYS_ADMIN_ROLE.equals(role.getRole())
				        && removeDirectPrivilege(role, externalRead)) {
					userService.saveRole(role);
				}
			}
			if (addDirectPrivilege(sysAdmin, externalRead)) {
				userService.saveRole(sysAdmin);
			}
			if (!sysAdmin.getPrivileges().contains(externalRead)) {
				throw new IllegalStateException("Sys-admin external configuration privilege was not persisted");
			}
			for (Role role : userService.getAllRoles()) {
				if (!DaherEndAdmissionConstants.SYS_ADMIN_ROLE.equals(role.getRole())
				        && role.getPrivileges().contains(externalRead)) {
					throw new IllegalStateException(
					        "Unexpected direct external configuration privilege holder: " + role.getRole());
				}
			}
		}
		finally {
			Context.removeProxyPrivilege(PrivilegeConstants.MANAGE_ROLES);
			Context.removeProxyPrivilege(PrivilegeConstants.GET_PRIVILEGES);
			Context.removeProxyPrivilege(PrivilegeConstants.GET_ROLES);
		}
	}

	static boolean addDirectPrivilege(Role role, Privilege privilege) {
		if (role == null || privilege == null || role.getPrivileges().contains(privilege)) {
			return false;
		}
		role.addPrivilege(privilege);
		return true;
	}

	static boolean removeDirectPrivilege(Role role, Privilege privilege) {
		if (role == null || privilege == null || !role.getPrivileges().contains(privilege)) {
			return false;
		}
		role.removePrivilege(privilege);
		return true;
	}
}
