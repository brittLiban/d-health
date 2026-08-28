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

import org.hibernate.LockMode;
import org.hibernate.LockOptions;
import org.hibernate.Session;
import org.hibernate.SessionFactory;
import org.openmrs.Visit;

/**
 * Wired via moduleApplicationContext.xml with a `sessionFactory` property -- the same pattern
 * bedmanagement's own BedManagementDaoImpl uses, reusing OpenMRS's existing thread-bound Hibernate
 * session (see ADR-030's transaction-map analysis in PROJECT_STATE.md for why that sharing matters
 * here).
 */
public class DaherEndAdmissionDaoImpl implements DaherEndAdmissionDao {

	private SessionFactory sessionFactory;

	public void setSessionFactory(SessionFactory sessionFactory) {
		this.sessionFactory = sessionFactory;
	}

	@Override
	public Visit getVisitByUuidForUpdate(String uuid) {
		Session session = sessionFactory.getCurrentSession();

		// Cheap, lock-free lookup of the numeric id first: Session.get(Class, id, LockOptions)
		// is the stable, non-deprecated way to acquire a row lock across the Hibernate 5.x line,
		// unlike alias-based Query#setLockMode which has shifted across minor versions.
		Integer visitId = (Integer) session.createQuery("select v.visitId from Visit v where v.uuid = :uuid")
		        .setParameter("uuid", uuid).uniqueResult();
		if (visitId == null) {
			return null;
		}

		return (Visit) session.get(Visit.class, visitId, new LockOptions(LockMode.PESSIMISTIC_WRITE));
	}
}
