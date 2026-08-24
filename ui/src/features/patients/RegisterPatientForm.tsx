import { type FormEvent, useEffect, useState } from 'react'
import type { OpenMrsPatient } from '../../api/types'
import { Button, Card, ErrorState, LoadingSkeleton, Select, TextInput } from '../../design-system'
import { useRegisterPatient } from './useRegisterPatient'

interface RegisterPatientFormProps {
  initialQuery: string
  onCancel: () => void
  onCreated: (patient: OpenMrsPatient) => void
}

// UI-012 §15: kept small on purpose — only the fields verified against this
// pinned stack (docs/UI-001-CLINICAL-UX.md §13a.2), never a field OpenMRS
// merely supports theoretically. No UUID, identifier-source, or FHIR
// terminology ever reaches this component's own text.
export function RegisterPatientForm({ initialQuery, onCancel, onCreated }: RegisterPatientFormProps) {
  // Prefill only when the search query unambiguously splits into two name
  // parts — a single token is just as likely to have been an identifier or
  // one half of a name, so it is left blank rather than guessed.
  const tokens = initialQuery.trim().split(/\s+/).filter(Boolean)
  const canPrefill = tokens.length === 2

  const [givenName, setGivenName] = useState(canPrefill ? tokens[0] : '')
  const [familyName, setFamilyName] = useState(canPrefill ? tokens[1] : '')
  const [gender, setGender] = useState<'' | 'M' | 'F'>('')
  const [birthdate, setBirthdate] = useState('')
  const [validationMessage, setValidationMessage] = useState<string | null>(null)

  const { state, register } = useRegisterPatient()

  useEffect(() => {
    if (state.status === 'success') {
      onCreated(state.patient)
    }
  }, [state, onCreated])

  const isBusy = state.status === 'submitting' || state.status === 'reconciling'

  const submit = () => {
    if (!givenName.trim() || !familyName.trim() || !gender || !birthdate) {
      setValidationMessage('Veuillez renseigner tous les champs.')
      return
    }
    setValidationMessage(null)
    void register({ givenName: givenName.trim(), familyName: familyName.trim(), gender, birthdate })
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    submit()
  }

  return (
    <Card style={{ maxWidth: '32rem' }}>
      <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-3)' }}>
        <h2 style={{ margin: 0 }}>Nouveau patient</h2>

        <TextInput
          label="Prénom"
          value={givenName}
          onChange={(event) => setGivenName(event.target.value)}
          disabled={isBusy}
          autoComplete="off"
        />
        <TextInput
          label="Nom"
          value={familyName}
          onChange={(event) => setFamilyName(event.target.value)}
          disabled={isBusy}
          autoComplete="off"
        />
        <Select
          label="Genre"
          value={gender}
          onChange={(event) => setGender(event.target.value as '' | 'M' | 'F')}
          disabled={isBusy}
        >
          <option value="">Sélectionner…</option>
          <option value="F">Féminin</option>
          <option value="M">Masculin</option>
        </Select>
        <TextInput
          label="Date de naissance"
          type="date"
          value={birthdate}
          onChange={(event) => setBirthdate(event.target.value)}
          disabled={isBusy}
        />

        {validationMessage ? (
          <span role="alert" style={{ color: 'var(--color-danger)' }}>
            {validationMessage}
          </span>
        ) : null}

        {state.status === 'submitting' ? <LoadingSkeleton label="Enregistrement en cours…" rows={1} /> : null}
        {state.status === 'reconciling' ? (
          <LoadingSkeleton label="Vérification de l'enregistrement…" rows={1} />
        ) : null}
        {state.status === 'error' ? <ErrorState code={state.code} onRetry={submit} /> : null}

        <div style={{ display: 'flex', gap: 'var(--space-3)' }}>
          <Button type="submit" disabled={isBusy}>
            Enregistrer le patient
          </Button>
          <Button type="button" variant="secondary" onClick={onCancel} disabled={isBusy}>
            Annuler
          </Button>
        </div>
      </form>
    </Card>
  )
}
