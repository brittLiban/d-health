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

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;
import static org.junit.Assert.fail;

import java.util.HashSet;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicBoolean;

import org.junit.Test;

public class DaherProxyPrivilegeExecutorTest {

	@Test
	public void execute_removesPrivilegeAfterSuccess() {
		RecordingExecutor executor = new RecordingExecutor();
		String result = executor.execute("Get Global Properties", () -> {
			assertTrue(executor.has("Get Global Properties"));
			return "done";
		});

		assertEquals("done", result);
		assertFalse(executor.has("Get Global Properties"));
	}

	@Test
	public void execute_removesPrivilegeAfterException() {
		RecordingExecutor executor = new RecordingExecutor();
		try {
			executor.execute("Get Global Properties", () -> {
				assertTrue(executor.has("Get Global Properties"));
				throw new IllegalStateException("synthetic failure");
			});
			fail("expected synthetic exception");
		}
		catch (IllegalStateException expected) {
			assertEquals("synthetic failure", expected.getMessage());
		}
		assertFalse(executor.has("Get Global Properties"));
	}

	@Test
	public void execute_concurrentThreadsNeverSeeEachOthersPrivilege() throws Exception {
		RecordingExecutor executor = new RecordingExecutor();
		CountDownLatch bothInside = new CountDownLatch(2);
		CountDownLatch release = new CountDownLatch(1);
		AtomicBoolean leak = new AtomicBoolean(false);

		Thread first = thread(executor, "Get Global Properties", "Get Concepts", bothInside, release, leak);
		Thread second = thread(executor, "Get Concepts", "Get Global Properties", bothInside, release, leak);
		first.start();
		second.start();
		bothInside.await();
		release.countDown();
		first.join();
		second.join();

		assertFalse("one thread observed the other thread's temporary privilege", leak.get());
		assertFalse(executor.has("Get Global Properties"));
		assertFalse(executor.has("Get Concepts"));
	}

	private static Thread thread(RecordingExecutor executor, String own, String other, CountDownLatch bothInside,
	        CountDownLatch release, AtomicBoolean leak) {
		return new Thread(() -> executor.execute(own, () -> {
			if (!executor.has(own) || executor.has(other)) {
				leak.set(true);
			}
			bothInside.countDown();
			try {
				release.await();
			}
			catch (InterruptedException e) {
				Thread.currentThread().interrupt();
				leak.set(true);
			}
			return null;
		}));
	}

	private static class RecordingExecutor extends DaherProxyPrivilegeExecutor {

		private final ThreadLocal<Set<String>> privileges = ThreadLocal.withInitial(HashSet::new);

		@Override
		protected void add(String privilege) {
			privileges.get().add(privilege);
		}

		@Override
		protected void remove(String privilege) {
			privileges.get().remove(privilege);
		}

		boolean has(String privilege) {
			return privileges.get().contains(privilege);
		}
	}
}
