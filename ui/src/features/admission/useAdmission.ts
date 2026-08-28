import { useCallback, useEffect, useRef, useState } from 'react'
import {
  createAdmissionEncounter,
  createMaternityVisit,
  getActiveVisits,
  getSession,
  verifyAdmissionMetadata,
} from '../../api/client'
import { ApiError, type ErrorCode } from '../../api/types'
import { deriveAdmissionState, type AdmissionState } from './deriveAdmissionState'
import { deriveReadiness, type AdmissionReadiness } from './deriveReadiness'

export type AdmissionReadState =
  | { kind: 'loading' }
  // §23: a narrow role (Reception) intentionally cannot read Visit/Encounter
  // state — not broadened, shown as neutral/unavailable rather than an error.
  | { kind: 'unavailable' }
  | { kind: 'read_error'; code: ErrorCode }
  | ({ kind: 'resolved' } & AdmissionState)

interface UseAdmissionResult {
  read: AdmissionReadState
  readiness: AdmissionReadiness | null
  pending: boolean
  mutationError: ErrorCode | null
  admit: () => Promise<void>
  resume: () => Promise<void>
  // UI-015F §13 — returns the freshly resolved state, not just void, so a
  // caller (EndAdmissionPanel's AMBIGUOUS-write resolution) can inspect what
  // reconciliation actually found rather than only triggering a re-render.
  refresh: () => Promise<AdmissionReadState>
}

function codeOf(error: unknown): ErrorCode {
  return error instanceof ApiError ? error.code : 'SERVER_ERROR'
}

// §14/§17 — the Encounter half of the two-write sequence, shared by a fresh
// admission (after the Visit is confirmed) and a resume (Encounter-only,
// never a second Visit). Always reconciles via an authoritative read after
// the attempt, whether the POST threw AMBIGUOUS_WRITE or returned 2xx —
// ADR-017C's write-succeeds/response-lies trap means a clean-looking
// response is not proof either (§12).
async function createEncounterAndReconcile(
  patientUuid: string,
  visitUuid: string,
  setRead: (state: AdmissionReadState) => void,
  setMutationError: (code: ErrorCode | null) => void,
) {
  try {
    await createAdmissionEncounter(patientUuid, visitUuid)
  } catch (error) {
    if (!(error instanceof ApiError) || error.code !== 'AMBIGUOUS_WRITE') {
      // A definite pre-save rejection (NOT_AUTHORIZED/VALIDATION_ERROR) —
      // the server already answered synchronously; no reconciliation to do.
      setMutationError(codeOf(error))
      return
    }
  }

  let afterEncounter: AdmissionState
  try {
    afterEncounter = deriveAdmissionState(await getActiveVisits(patientUuid))
  } catch (error) {
    setMutationError(codeOf(error))
    return
  }
  setRead({ kind: 'resolved', ...afterEncounter })
  if (afterEncounter.status === 'PARTIAL_VISIT') {
    // §17 — zero linked Encounters after reconciliation. PARTIAL_VISIT is
    // already the correct, resumable displayed state; surface the
    // uncertainty too, rather than silently doing nothing.
    setMutationError('AMBIGUOUS_WRITE')
  }
}

export function useAdmission(patientUuid: string): UseAdmissionResult {
  const [read, setRead] = useState<AdmissionReadState>({ kind: 'loading' })
  const [readiness, setReadiness] = useState<AdmissionReadiness | null>(null)
  const [pending, setPending] = useState(false)
  const [mutationError, setMutationError] = useState<ErrorCode | null>(null)
  // §22: one mutation promise at a time, same-browser. A ref (not state) so
  // the guard is checked synchronously, before the first `await` yields —
  // a second click/Enter before React re-renders still sees it set.
  const inFlight = useRef(false)

  const loadReadiness = useCallback(async () => {
    try {
      const session = await getSession()
      setReadiness(deriveReadiness(session))
    } catch {
      // Readiness failing to load never blocks the admission-state read
      // below; it only means mutation actions stay hidden — §5's fail-
      // closed default: no readiness proven, no action offered.
      setReadiness(null)
    }
  }, [])

  const loadAdmissionState = useCallback(async (): Promise<AdmissionReadState> => {
    try {
      const visits = await getActiveVisits(patientUuid)
      const resolved: AdmissionReadState = { kind: 'resolved', ...deriveAdmissionState(visits) }
      setRead(resolved)
      return resolved
    } catch (error) {
      if (error instanceof ApiError && error.code === 'NOT_AUTHORIZED') {
        const unavailable: AdmissionReadState = { kind: 'unavailable' }
        setRead(unavailable)
        return unavailable
      }
      const readError: AdmissionReadState = { kind: 'read_error', code: codeOf(error) }
      setRead(readError)
      return readError
    }
  }, [patientUuid])

  const refresh = useCallback(async (): Promise<AdmissionReadState> => {
    setRead({ kind: 'loading' })
    setMutationError(null)
    try {
      // §3 — a drift check only; never the source of the mutation payload.
      // Fails closed on missing/duplicate/mismatched/retired metadata rather
      // than silently proceeding against whatever the pinned constants say.
      await verifyAdmissionMetadata()
    } catch (error) {
      const readError: AdmissionReadState = { kind: 'read_error', code: codeOf(error) }
      setRead(readError)
      setReadiness(null)
      return readError
    }
    const [, admissionState] = await Promise.all([loadReadiness(), loadAdmissionState()])
    return admissionState
  }, [loadReadiness, loadAdmissionState])

  useEffect(() => {
    void refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [patientUuid])

  const admit = useCallback(async () => {
    if (inFlight.current) return
    inFlight.current = true
    setPending(true)
    setMutationError(null)
    try {
      // §12 preflight — never fire on stale state.
      let preflightVisits
      try {
        preflightVisits = await getActiveVisits(patientUuid)
      } catch (error) {
        setMutationError(codeOf(error))
        return
      }
      const preflight = deriveAdmissionState(preflightVisits)
      if (preflight.status !== 'NOT_ADMITTED') {
        // Server truth moved since the last read (already admitted, already
        // partial, or ambiguous) — never blind-POST over it.
        setRead({ kind: 'resolved', ...preflight })
        return
      }

      try {
        await createMaternityVisit(patientUuid)
      } catch (error) {
        if (!(error instanceof ApiError) || error.code !== 'AMBIGUOUS_WRITE') {
          setMutationError(codeOf(error))
          return
        }
      }

      // §12/§16 — reconcile/read Visit, authoritative and unconditional;
      // never trusts the POST response alone.
      let afterVisit: AdmissionState
      try {
        afterVisit = deriveAdmissionState(await getActiveVisits(patientUuid))
      } catch (error) {
        setMutationError(codeOf(error))
        return
      }
      setRead({ kind: 'resolved', ...afterVisit })

      if (afterVisit.status === 'AMBIGUOUS' || afterVisit.status === 'COMPLETE') return
      if (afterVisit.status === 'NOT_ADMITTED') {
        // §16 — zero matches: unresolved, not a failure. The operator may
        // explicitly retry (a fresh `admit()` click); never automatic here.
        setMutationError('AMBIGUOUS_WRITE')
        return
      }

      // PARTIAL_VISIT — exactly one confirmed Visit, no Encounter yet.
      await createEncounterAndReconcile(patientUuid, afterVisit.visit.uuid, setRead, setMutationError)
    } finally {
      inFlight.current = false
      setPending(false)
    }
  }, [patientUuid])

  const resume = useCallback(async () => {
    if (inFlight.current) return
    inFlight.current = true
    setPending(true)
    setMutationError(null)
    try {
      // §18 — re-derive from server truth, never from what React remembers.
      let visits
      try {
        visits = await getActiveVisits(patientUuid)
      } catch (error) {
        setMutationError(codeOf(error))
        return
      }
      const current = deriveAdmissionState(visits)
      setRead({ kind: 'resolved', ...current })
      // §18/§19 — never create another Visit during recovery: resume() only
      // ever calls createEncounterAndReconcile, never createMaternityVisit.
      if (current.status !== 'PARTIAL_VISIT') return
      await createEncounterAndReconcile(patientUuid, current.visit.uuid, setRead, setMutationError)
    } finally {
      inFlight.current = false
      setPending(false)
    }
  }, [patientUuid])

  return { read, readiness, pending, mutationError, admit, resume, refresh }
}
