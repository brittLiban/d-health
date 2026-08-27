import type { OpenMrsPatient } from '../../api/types'
import { Button, ConnectionState, EmptyState, ErrorState, LoadingSkeleton } from '../../design-system'
import { MaternityPatientCard } from './MaternityPatientCard'
import { useMaternityBoard } from './useMaternityBoard'

interface MaternityBoardPageProps {
  onOpenPatient: (patient: OpenMrsPatient) => void
}

// UI-014 — replaces the former "Bientôt" placeholder for the Maternité nav
// item. Read-only: no ending an admission, no ward-bed operations, no
// clinical documentation of any kind (§10, §13). French-first throughout (§4).
export function MaternityBoardPage({ onOpenPatient }: MaternityBoardPageProps) {
  const { state, refresh } = useMaternityBoard()

  return (
    <div style={{ padding: 'var(--space-5)', display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 'var(--space-3)' }}>
        <h1>Maternité</h1>
        <Button variant="secondary" onClick={() => void refresh()}>
          Actualiser
        </Button>
      </div>
      <p>Patientes actuellement admises</p>

      {state.kind === 'loading' && <LoadingSkeleton label="Chargement des admissions…" />}

      {/* §3 — a neutral unavailable state for a role that cannot read this
          (Reception), never an alarming error and never a broadened
          privilege to make the read succeed. */}
      {state.kind === 'unavailable' && (
        <EmptyState title="Ce tableau n'est pas disponible avec ce compte">
          Contactez un administrateur si vous pensez que cet accès est nécessaire.
        </EmptyState>
      )}

      {state.kind === 'error' && state.code === 'NETWORK_ERROR' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)', alignItems: 'flex-start' }}>
          <ConnectionState status="connection-lost" />
          <Button variant="secondary" onClick={() => void refresh()}>
            Réessayer
          </Button>
        </div>
      )}

      {state.kind === 'error' && state.code !== 'NETWORK_ERROR' && (
        <ErrorState code={state.code} onRetry={() => void refresh()} />
      )}

      {state.kind === 'resolved' && state.entries.length === 0 && (
        <EmptyState title="Aucune patiente actuellement admise en maternité.">
          Cette liste reflète uniquement les admissions actives à Maternité — d'autres patientes
          peuvent exister ailleurs dans l'établissement.
        </EmptyState>
      )}

      {state.kind === 'resolved' && state.entries.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
          {state.truncated ? (
            <p role="status">
              D'autres patientes admises existent au-delà de celles affichées ci-dessous.
            </p>
          ) : null}
          {state.entries.map((entry) => (
            <MaternityPatientCard key={entry.patient.uuid} entry={entry} onOpen={onOpenPatient} />
          ))}
        </div>
      )}
    </div>
  )
}
