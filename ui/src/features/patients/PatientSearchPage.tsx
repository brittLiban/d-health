import { type FormEvent, useState } from 'react'
import type { OpenMrsPatient } from '../../api/types'
import {
  Button,
  Card,
  ConnectionState,
  EmptyState,
  ErrorState,
  LoadingSkeleton,
  PatientCard,
  SearchInput,
} from '../../design-system'
import { usePatientSearch } from './usePatientSearch'

interface PatientSearchPageProps {
  onOpenPatient: (patient: OpenMrsPatient) => void
}

export function PatientSearchPage({ onOpenPatient }: PatientSearchPageProps) {
  const [query, setQuery] = useState('')
  const { state, search } = usePatientSearch()

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    void search(query)
  }

  return (
    <div style={{ padding: 'var(--space-5)', display: 'flex', flexDirection: 'column', gap: 'var(--space-4)' }}>
      <h1>Patients</h1>
      <form onSubmit={handleSubmit} style={{ maxWidth: '32rem' }}>
        <SearchInput
          label="Rechercher un patient"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Nom ou identifiant"
        />
        <div style={{ marginTop: 'var(--space-3)' }}>
          <Button type="submit">Rechercher</Button>
        </div>
      </form>

      {state.status === 'loading' && <LoadingSkeleton label="Recherche en cours…" />}

      {state.status === 'error' && state.code === 'NETWORK_ERROR' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)', alignItems: 'flex-start' }}>
          <ConnectionState status="connection-lost" />
          <Button variant="secondary" onClick={() => void search(query)}>
            Réessayer
          </Button>
        </div>
      )}

      {state.status === 'error' && state.code !== 'NETWORK_ERROR' && (
        <ErrorState code={state.code} onRetry={() => void search(query)} />
      )}

      {state.status === 'success' && state.results.length === 0 && (
        <EmptyState title="Aucun patient trouvé">
          Aucun résultat pour « {query} ».
          <div style={{ marginTop: 'var(--space-3)' }}>
            <Button disabled title="L'enregistrement de patients n'est pas encore activé dans cette version.">
              Nouveau patient
            </Button>
          </div>
        </EmptyState>
      )}

      {state.status === 'success' && state.results.length > 0 && (
        <Card
          aria-label={`${state.results.length} résultat(s) de recherche`}
          style={{ background: 'transparent', border: 'none', padding: 0 }}
        >
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
            {state.results.map((patient) => (
              <PatientCard key={patient.uuid} patient={patient} onOpen={onOpenPatient} />
            ))}
          </div>
        </Card>
      )}
    </div>
  )
}
