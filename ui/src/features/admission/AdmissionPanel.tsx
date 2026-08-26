import { useEffect, useRef } from 'react'
import { Button, LoadingSkeleton, StatusBadge } from '../../design-system'
import { ErrorState } from '../../design-system/ErrorState'
import { useAdmission } from './useAdmission'

interface AdmissionPanelProps {
  patientUuid: string
}

// UI-013 — replaces the Résumé tab's former "Admission et contexte de
// grossesse non disponibles" placeholder. French-first (§11); never renders
// a Visit/Encounter UUID, REST path, HTTP status, or privilege name — every
// branch below is worded for an operator, not a developer (§11, §24).
export function AdmissionPanel({ patientUuid }: AdmissionPanelProps) {
  const { read, readiness, pending, mutationError, admit, resume, refresh } = useAdmission(patientUuid)
  const errorRef = useRef<HTMLDivElement>(null)

  // §26 — focus moves to the recovery/error state after a failure, so a
  // keyboard/screen-reader user is not left on the button that just acted.
  useEffect(() => {
    if (mutationError) errorRef.current?.focus()
  }, [mutationError])

  if (read.kind === 'loading') {
    return <LoadingSkeleton label="Chargement de l'état d'admission…" rows={2} />
  }

  if (read.kind === 'unavailable') {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
        <p>
          <strong>Admission :</strong> non disponible avec ce compte.
        </p>
      </div>
    )
  }

  if (read.kind === 'read_error') {
    return <ErrorState code={read.code} onRetry={() => void refresh()} />
  }

  const canAct = readiness?.status === 'READY' && !pending

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
      <div aria-live="polite">
        {read.status === 'NOT_ADMITTED' ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
            <p>
              <strong>Admission :</strong> <StatusBadge label="Non admise" tone="neutral" />
            </p>
            {readiness?.status === 'PROVIDER_MISSING' ? (
              <p role="alert">
                Votre compte n'est pas prêt pour effectuer une admission. Contactez un
                administrateur.
              </p>
            ) : null}
            {readiness?.status === 'READY' ? (
              <Button type="button" onClick={() => void admit()} disabled={!canAct}>
                {pending ? 'Admission en cours…' : 'Admettre'}
              </Button>
            ) : null}
          </div>
        ) : null}

        {read.status === 'PARTIAL_VISIT' ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
            <p>
              <strong>Admission :</strong> <StatusBadge label="Admission incomplète" tone="warning" />
            </p>
            {readiness?.status === 'READY' ? (
              <Button type="button" onClick={() => void resume()} disabled={!canAct}>
                {pending ? 'Admission en cours…' : "Reprendre l'admission"}
              </Button>
            ) : null}
          </div>
        ) : null}

        {read.status === 'COMPLETE' ? (
          <p>
            <strong>Admission :</strong> <StatusBadge label="Admise" tone="success" />
          </p>
        ) : null}

        {read.status === 'AMBIGUOUS' ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
            <p role="alert">
              <strong>Admission :</strong> Impossible de confirmer l'état de l'admission. Une
              vérification est nécessaire.
            </p>
            <Button type="button" variant="secondary" onClick={() => void refresh()} disabled={pending}>
              Vérifier à nouveau
            </Button>
          </div>
        ) : null}
      </div>

      {mutationError ? (
        <div ref={errorRef} tabIndex={-1}>
          <ErrorState code={mutationError} onRetry={() => void refresh()} />
        </div>
      ) : null}
    </div>
  )
}
