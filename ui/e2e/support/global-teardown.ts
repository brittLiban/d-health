import { unlinkSync } from 'node:fs'
import { deleteClient, deleteUser, getMasterAdminToken, readSetupEnv } from './adminApi'
import { readState } from './state'

/**
 * DH-CLINIC-004B: removes every ephemeral artifact this qualification run
 * created -- the temporary setup client (its service account goes with it)
 * and the three synthetic users. The two synthetic provider organizations
 * are left in place: the Exchange API has no organization-delete endpoint
 * (by design -- DH-EX-002 never exposes destructive organization operations),
 * and this is expected/acceptable because the qualification run's entire
 * Postgres volume is itself thrown away at the end of the CI job (see
 * scripts/qualify-native-auth.sh). Runs best-effort: one failed cleanup call
 * must not hide the pass/fail result of the actual qualification suite.
 */
export default async function globalTeardown(): Promise<void> {
  const env = readSetupEnv()
  let state
  try {
    state = readState()
  } catch {
    return
  }

  // A fresh admin token is minted rather than reusing global-setup's, which
  // has almost certainly expired by the time the full suite has finished.
  const adminToken = await getMasterAdminToken(env)
  const attempts: Array<Promise<unknown>> = [
    deleteClient(env, adminToken, state.setupClient.uuid),
    deleteUser(env, adminToken, state.clinicianA.id),
    deleteUser(env, adminToken, state.clinicianB.id),
    deleteUser(env, adminToken, state.unassigned.id),
  ]
  const results = await Promise.allSettled(attempts)
  for (const result of results) {
    if (result.status === 'rejected') {
      // eslint-disable-next-line no-console
      console.warn('DH-CLINIC-004B teardown: a cleanup call failed (non-fatal):', result.reason)
    }
  }

  try {
    unlinkSync(process.env.E2E_STATE_FILE!)
  } catch {
    // already gone / never written -- fine
  }
}
