import { useCallback, useState } from 'react'
import {
  createPatient,
  discoverIdentifierSource,
  findPatientByIdentifier,
  generateIdentifier,
} from '../../api/client'
import { ApiError, type ErrorCode, type OpenMrsPatient, type PatientRegistration } from '../../api/types'

export type RegisterPatientState =
  | { status: 'idle' }
  | { status: 'submitting' }
  | { status: 'reconciling' }
  | { status: 'success'; patient: OpenMrsPatient }
  | { status: 'error'; code: ErrorCode }

function codeOf(error: unknown): ErrorCode {
  return error instanceof ApiError ? error.code : 'SERVER_ERROR'
}

export function useRegisterPatient() {
  const [state, setState] = useState<RegisterPatientState>({ status: 'idle' })

  // UI-012 §14: never an automatic retry. Every call here is a fresh
  // attempt started only because a human clicked something — a brand new
  // identifier is generated each time, never reused across attempts.
  const register = useCallback(async (registration: PatientRegistration) => {
    setState({ status: 'submitting' })

    let source
    try {
      source = await discoverIdentifierSource()
    } catch (error) {
      setState({ status: 'error', code: codeOf(error) })
      return
    }

    let identifier: string
    try {
      identifier = await generateIdentifier(source.uuid)
    } catch (error) {
      setState({ status: 'error', code: codeOf(error) })
      return
    }

    try {
      const patient = await createPatient(registration, identifier, source)
      setState({ status: 'success', patient })
      return
    } catch (error) {
      if (!(error instanceof ApiError) || error.code !== 'AMBIGUOUS_WRITE') {
        // A definite rejection (validation, authorization, conflict) — the
        // server answered synchronously that nothing was created. No
        // reconciliation needed; the answer is already known.
        setState({ status: 'error', code: codeOf(error) })
        return
      }
    }

    // The POST's own outcome is unknown (dropped connection, timeout,
    // aborted response). The identifier was generated server-side before
    // the POST, so it is the one fact both sides can agree on — use it to
    // ask OpenMRS's own authoritative state, rather than guessing or
    // re-submitting.
    setState({ status: 'reconciling' })
    let matches: OpenMrsPatient[]
    try {
      matches = await findPatientByIdentifier(identifier)
    } catch (error) {
      setState({ status: 'error', code: codeOf(error) })
      return
    }

    if (matches.length === 1) {
      setState({ status: 'success', patient: matches[0] })
    } else if (matches.length === 0) {
      // Explicitly unresolved, not a failure and not a success: the
      // identifier UNIQUE constraint plus this exact-match query means
      // OpenMRS genuinely never committed it. Safe for a human to decide to
      // retry — this hook will never do it on its own.
      setState({ status: 'error', code: 'AMBIGUOUS_WRITE' })
    } else {
      // More than one match for a UNIQUE identifier is not a state this
      // reconciliation can safely interpret — stop rather than pick one.
      setState({ status: 'error', code: 'SERVER_ERROR' })
    }
  }, [])

  const reset = useCallback(() => setState({ status: 'idle' }), [])

  return { state, register, reset }
}
