import { useCallback, useRef, useState } from 'react'
import { endAdmission } from '../../api/client'
import { ApiError, type ErrorCode } from '../../api/types'
import type { AdmissionReadState } from './useAdmission'

// UI-015F §2 — the eight required states, exactly. ACTIVE/CONFIRMATION/
// SUBMITTING are pure UI progression (no server call has resolved yet).
// ENDED and ALREADY_ENDED are deliberately distinct (§9's "zero write" vs a
// real mutation), each carrying the server's own stopDatetime when it has
// one. AMBIGUOUS is reserved for "cannot be authoritatively determined" —
// never the default for any 4xx/5xx that DID answer synchronously.
export type EndAdmissionState =
  | { kind: 'ACTIVE' }
  | { kind: 'CONFIRMATION' }
  | { kind: 'SUBMITTING' }
  | { kind: 'ENDED'; stopDatetime: string | null }
  | { kind: 'ALREADY_ENDED'; stopDatetime: string | null }
  | { kind: 'AMBIGUOUS' }
  | { kind: 'UNAVAILABLE' }
  | { kind: 'ERROR'; code: ErrorCode }

interface UseEndAdmissionResult {
  state: EndAdmissionState
  open: () => void
  cancel: () => void
  confirm: () => Promise<void>
  retry: () => Promise<void>
  // ENDED/ALREADY_ENDED's own explicit "Fermer" action -- reconciles, then
  // unconditionally returns to ACTIVE (the caller's own parent branch will
  // already have moved on by then, since the Visit is no longer COMPLETE).
  dismiss: () => Promise<void>
  // AMBIGUOUS's own "Vérifier à nouveau" -- re-runs the same
  // reconcile-and-classify logic the original ambiguous response triggered,
  // landing on ENDED/ERROR/AMBIGUOUS again depending on what is found; never
  // resubmits the mutation itself (§13's "never blind retry").
  verifyAgain: () => Promise<void>
}

function codeOf(error: unknown): ErrorCode {
  return error instanceof ApiError ? error.code : 'SERVER_ERROR'
}

// UI-015/ADR-030 — "Terminer l'admission". No preflight read is needed before
// the POST (unlike useAdmission's admit()): the endpoint itself is the
// authoritative re-validator (visit type/location/state) and is idempotent
// on an already-ended Visit, so a stale local read can never cause a wrong
// mutation here the way it could for admit()/resume().
//
// `reconcile` is the caller's authoritative admission-state refresh
// (useAdmission's own refresh()) — it RETURNS the resolved state, not just
// void, specifically so the AMBIGUOUS-write resolution below (§13) can
// inspect what reconciliation actually found rather than guessing from the
// mutation response alone.
export function useEndAdmission(
  visitUuid: string,
  reconcile: () => Promise<AdmissionReadState>,
): UseEndAdmissionResult {
  const [state, setState] = useState<EndAdmissionState>({ kind: 'ACTIVE' })
  // One mutation promise at a time, checked synchronously before the first
  // await — same guard useAdmission's admit()/resume() use. SUBMITTING
  // itself (the state) is what disables the Confirmer button in the UI, but
  // this ref is the actual correctness guard against a double-fire.
  const inFlight = useRef(false)

  const open = useCallback(() => setState({ kind: 'CONFIRMATION' }), [])
  const cancel = useCallback(() => setState({ kind: 'ACTIVE' }), [])

  // §13 — the ambiguous-write resolution. A dropped connection or unparsable
  // response after the request may have already reached the server and
  // committed, so the mutation response alone is never trusted: an
  // authoritative read decides what actually happened. "Never blind retry"
  // means the mutation itself is never resent here — only reconciled.
  const resolveAmbiguous = useCallback(async () => {
    let resolved: AdmissionReadState
    try {
      resolved = await reconcile()
    } catch {
      setState({ kind: 'AMBIGUOUS' })
      return
    }
    if (resolved.kind === 'resolved' && resolved.status !== 'COMPLETE') {
      // The Visit is no longer an active matching admission -- it was
      // ended, even though the response describing that was lost.
      setState({ kind: 'ENDED', stopDatetime: null })
      return
    }
    if (resolved.kind === 'resolved' && resolved.status === 'COMPLETE') {
      // Still definitely active: the mutation did not go through. A known,
      // retryable failure, not an unresolved one.
      setState({ kind: 'ERROR', code: 'AMBIGUOUS_WRITE' })
      return
    }
    // The reconciliation read itself failed or returned something other
    // than a resolved admission state -- truly cannot be determined.
    setState({ kind: 'AMBIGUOUS' })
  }, [reconcile])

  const attempt = useCallback(async () => {
    if (inFlight.current) return
    inFlight.current = true
    setState({ kind: 'SUBMITTING' })
    try {
      const result = await endAdmission(visitUuid)
      setState({
        kind: result.status === 'ENDED' ? 'ENDED' : 'ALREADY_ENDED',
        stopDatetime: result.stopDatetime,
      })
    } catch (error) {
      const code = codeOf(error)
      if (code === 'AMBIGUOUS_WRITE') {
        await resolveAmbiguous()
      } else if (code === 'NOT_AUTHORIZED') {
        setState({ kind: 'UNAVAILABLE' })
      } else {
        setState({ kind: 'ERROR', code })
      }
    } finally {
      inFlight.current = false
    }
  }, [visitUuid, resolveAmbiguous])

  const confirm = useCallback(async () => {
    await attempt()
  }, [attempt])

  // ERROR's own recovery action: a genuine, known, retryable failure (never
  // AMBIGUOUS) — an explicit human click, not an automatic resend, so this
  // is not the "blind retry" §13 forbids. Resubmits directly rather than
  // routing back through CONFIRMATION, matching ErrorState's existing
  // onRetry convention used everywhere else in this feature.
  const retry = useCallback(async () => {
    await attempt()
  }, [attempt])

  // UNAVAILABLE/ERROR never mutated anything, so reconciling before
  // returning to ACTIVE is a courtesy, not a correctness requirement; for
  // ENDED/ALREADY_ENDED it IS the requirement (§12's "successful
  // authoritative reconciliation") -- deferred to this explicit dismissal,
  // not fired automatically, so the terminal message is never raced away
  // before the caller (and a test) can observe it.
  const dismiss = useCallback(async () => {
    await reconcile()
    setState({ kind: 'ACTIVE' })
  }, [reconcile])

  const verifyAgain = useCallback(async () => {
    await resolveAmbiguous()
  }, [resolveAmbiguous])

  return { state, open, cancel, confirm, retry, dismiss, verifyAgain }
}
