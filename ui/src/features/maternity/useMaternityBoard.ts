import { useCallback, useEffect, useRef, useState } from 'react'
import { getMaternityBoardVisits } from '../../api/client'
import { ApiError, type ErrorCode } from '../../api/types'
import { deriveMaternityBoard, type MaternityBoardEntry } from './deriveMaternityBoard'

export type MaternityBoardState =
  | { kind: 'loading' }
  // §3 — Reception cannot read Visit/Encounter state (missing "Get Visit
  // Types"/"Get Visits", confirmed live) and must never be broadened to
  // make this screen work; a 403 renders as a neutral unavailable state,
  // the same pattern useAdmission already established.
  | { kind: 'unavailable' }
  | { kind: 'error'; code: ErrorCode }
  | { kind: 'resolved'; entries: MaternityBoardEntry[]; truncated: boolean }

export function useMaternityBoard() {
  const [state, setState] = useState<MaternityBoardState>({ kind: 'loading' })
  // Guards against a slow earlier fetch overwriting a faster later one,
  // matching usePatientSearch's own convention.
  const requestId = useRef(0)

  const refresh = useCallback(async () => {
    const thisRequestId = ++requestId.current
    setState({ kind: 'loading' })
    try {
      const { visits, truncated } = await getMaternityBoardVisits()
      if (requestId.current !== thisRequestId) return
      setState({ kind: 'resolved', entries: deriveMaternityBoard(visits), truncated })
    } catch (error) {
      if (requestId.current !== thisRequestId) return
      if (error instanceof ApiError && error.code === 'NOT_AUTHORIZED') {
        setState({ kind: 'unavailable' })
        return
      }
      setState({ kind: 'error', code: error instanceof ApiError ? error.code : 'SERVER_ERROR' })
    }
  }, [])

  // §9 — refetch on screen entry. App.tsx unmounts this component when the
  // operator navigates away (same pattern as the Patients tab), so
  // returning to Maternité remounts it and this effect fires again; no
  // polling, no focus/visibility listener, no websocket.
  useEffect(() => {
    void refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return { state, refresh }
}
