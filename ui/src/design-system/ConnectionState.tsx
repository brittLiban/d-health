import styles from './ConnectionState.module.css'

// The exact write-path vocabulary from docs/UI-001-CLINICAL-UX.md §16, corrected
// in UI-001A: a dropped connection after submission is never rendered as
// "not saved" — the server may already have accepted it — so this component
// only ever says the status is unknown until a re-fetch resolves it. This
// ticket performs no writes, so only "connection-lost" is exercised today
// (a search request that timed out); the others exist so a future write
// screen reuses this vocabulary instead of inventing its own.
export type ConnectionStatus = 'saving' | 'saved' | 'save-failed' | 'connection-lost'

const COPY: Record<ConnectionStatus, string> = {
  saving: 'Enregistrement…',
  saved: 'Enregistré',
  'save-failed': 'Échec de l’enregistrement',
  'connection-lost': 'Connexion perdue — état inconnu jusqu’à la reconnexion',
}

const CLASS: Record<ConnectionStatus, string> = {
  saving: styles.saving,
  saved: styles.saved,
  'save-failed': styles.saveFailed,
  'connection-lost': styles.connectionLost,
}

interface ConnectionStateProps {
  status: ConnectionStatus
}

export function ConnectionState({ status }: ConnectionStateProps) {
  return (
    <div className={[styles.banner, CLASS[status]].join(' ')} role="status" aria-live="polite">
      {COPY[status]}
    </div>
  )
}
