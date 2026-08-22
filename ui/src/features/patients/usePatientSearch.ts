import { useCallback, useRef, useState } from 'react'
import { searchPatients } from '../../api/client'
import { ApiError, type ErrorCode, type OpenMrsPatient } from '../../api/types'

export type PatientSearchState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'success'; results: OpenMrsPatient[] }
  | { status: 'error'; code: ErrorCode }

export function usePatientSearch() {
  const [state, setState] = useState<PatientSearchState>({ status: 'idle' })
  // Guards against a slow earlier request overwriting a faster later one —
  // only the most recent search's result is ever committed to state.
  const requestId = useRef(0)

  const search = useCallback(async (query: string) => {
    const trimmed = query.trim()
    if (!trimmed) {
      setState({ status: 'idle' })
      return
    }
    const thisRequestId = ++requestId.current
    setState({ status: 'loading' })
    try {
      const results = await searchPatients(trimmed)
      if (requestId.current === thisRequestId) {
        setState({ status: 'success', results })
      }
    } catch (error) {
      if (requestId.current !== thisRequestId) return
      const code = error instanceof ApiError ? error.code : 'SERVER_ERROR'
      setState({ status: 'error', code })
    }
  }, [])

  return { state, search }
}
