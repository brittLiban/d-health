import { useEffect, useRef } from 'react'
import { Button } from '../../design-system'
import { ErrorState } from '../../design-system/ErrorState'
import type { AdmissionReadiness } from './deriveReadiness'
import { useEndAdmission } from './useEndAdmission'
import type { AdmissionReadState } from './useAdmission'

interface EndAdmissionPanelProps {
  visitUuid: string
  readiness: AdmissionReadiness | null
  // The caller's authoritative admission-state refresh (useAdmission's own
  // refresh()), returning what it found -- this panel never decides on its
  // own that the admission ended; it only ever reflects what an
  // authoritative read says (see useEndAdmission's AMBIGUOUS resolution).
  reconcile: () => Promise<AdmissionReadState>
}

// UI-015/ADR-030 — "Terminer l'admission". Rendered only from AdmissionPanel's
// COMPLETE branch (an active Daher Maternity Admission Visit exists). Sets
// the Visit's stopDatetime and nothing else (DECISIONS.md ADR-030's own
// narrow scope decision). Implements the eight required states (UI-015F §2):
// ACTIVE, CONFIRMATION, SUBMITTING, ENDED, ALREADY_ENDED, AMBIGUOUS,
// UNAVAILABLE, ERROR -- see useEndAdmission for the state machine itself.
export function EndAdmissionPanel({ visitUuid, readiness, reconcile }: EndAdmissionPanelProps) {
  const { state, open, cancel, confirm, retry, dismiss, verifyAgain } = useEndAdmission(visitUuid, reconcile)
  const focusRef = useRef<HTMLDivElement>(null)

  // §26-equivalent: focus moves to whichever non-ACTIVE state just appeared,
  // so a keyboard/screen-reader user is never left on a button that no
  // longer reflects what happened.
  useEffect(() => {
    if (state.kind !== 'ACTIVE') focusRef.current?.focus()
  }, [state.kind])

  if (state.kind === 'ACTIVE') {
    const canAct = readiness?.status === 'READY'
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
        {readiness?.status === 'PROVIDER_MISSING' ? (
          <p role="alert">
            Votre compte n'est pas prêt pour terminer une admission. Contactez un administrateur.
          </p>
        ) : null}
        <Button type="button" variant="secondary" onClick={open} disabled={!canAct}>
          Terminer l'admission
        </Button>
      </div>
    )
  }

  if (state.kind === 'CONFIRMATION' || state.kind === 'SUBMITTING') {
    const submitting = state.kind === 'SUBMITTING'
    return (
      <div
        ref={focusRef}
        tabIndex={-1}
        role="alertdialog"
        aria-label="Confirmer la fin de l'admission"
        style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}
      >
        <p>Terminer cette admission en maternité ?</p>
        <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
          <Button type="button" onClick={() => void confirm()} disabled={submitting}>
            {submitting ? 'Fin en cours…' : 'Confirmer'}
          </Button>
          <Button type="button" variant="secondary" onClick={cancel} disabled={submitting}>
            Annuler
          </Button>
        </div>
      </div>
    )
  }

  if (state.kind === 'ENDED' || state.kind === 'ALREADY_ENDED') {
    return (
      <div ref={focusRef} tabIndex={-1} role="status" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
        <p>
          {state.kind === 'ENDED'
            ? 'Admission terminée.'
            : 'Cette admission était déjà terminée.'}
        </p>
        <Button type="button" variant="secondary" onClick={() => void dismiss()}>
          Fermer
        </Button>
      </div>
    )
  }

  if (state.kind === 'AMBIGUOUS') {
    return (
      <div ref={focusRef} tabIndex={-1} role="alert" style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
        <p>
          Impossible de confirmer si l'admission a été terminée. Une vérification est
          nécessaire.
        </p>
        <Button type="button" variant="secondary" onClick={() => void verifyAgain()}>
          Vérifier à nouveau
        </Button>
      </div>
    )
  }

  if (state.kind === 'UNAVAILABLE') {
    return (
      <div ref={focusRef} tabIndex={-1} role="alert">
        <p>Vous n'êtes pas autorisé(e) à terminer cette admission.</p>
      </div>
    )
  }

  // state.kind === 'ERROR'
  return (
    <div ref={focusRef} tabIndex={-1}>
      <ErrorState code={state.code} onRetry={() => void retry()} />
    </div>
  )
}
