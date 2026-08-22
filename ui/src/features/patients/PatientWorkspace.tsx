import { useState } from 'react'
import type { OpenMrsPatient } from '../../api/types'
import { Button, EmptyState, PatientHeader } from '../../design-system'
import { formatBirthInfo, formatIdentifier } from '../../design-system/formatPatient'
import styles from './PatientWorkspace.module.css'

const TABS = [
  'Résumé',
  'Grossesse',
  'Travail & Accouchement',
  'Ordonnances & Résultats',
  'Documents',
] as const

type Tab = (typeof TABS)[number]

interface PatientWorkspaceProps {
  patient: OpenMrsPatient
  onBack: () => void
}

// Only Résumé renders real data — the exact fields UI-001A confirmed the
// patient-search response carries (docs/UI-001-CLINICAL-UX.md §13a.1). Every
// other tab is an honest "not yet available" rather than invented pregnancy,
// labour, or lab content (§7, AGENTS.md's "never invent clinical content").
export function PatientWorkspace({ patient, onBack }: PatientWorkspaceProps) {
  const [activeTab, setActiveTab] = useState<Tab>('Résumé')

  return (
    <div>
      <div style={{ padding: 'var(--space-3) var(--space-4) 0' }}>
        <Button variant="secondary" onClick={onBack}>
          ← Retour à la recherche
        </Button>
      </div>
      <PatientHeader patient={patient} />
      <div className={styles.tabs} role="tablist" aria-label="Sections du dossier patient">
        {TABS.map((tab) => (
          <button
            key={tab}
            type="button"
            role="tab"
            id={`tab-${tab}`}
            aria-selected={activeTab === tab}
            aria-controls={`panel-${tab}`}
            className={[styles.tab, activeTab === tab ? styles.tabActive : ''].join(' ')}
            onClick={() => setActiveTab(tab)}
          >
            {tab}
          </button>
        ))}
      </div>
      <div
        role="tabpanel"
        id={`panel-${activeTab}`}
        aria-labelledby={`tab-${activeTab}`}
        className={styles.panel}
      >
        {activeTab === 'Résumé' ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
            <p>
              <strong>Identifiant :</strong> {formatIdentifier(patient)}
            </p>
            <p>
              <strong>Naissance :</strong> {formatBirthInfo(patient)}
            </p>
            <p>
              <strong>Genre :</strong> {patient.person?.gender ?? 'Non renseigné'}
            </p>
            <EmptyState title="Admission et contexte de grossesse non disponibles">
              Ces informations nécessitent un appel API distinct, non couvert par cette version.
            </EmptyState>
          </div>
        ) : (
          <EmptyState title="Cet écran n'est pas encore disponible">
            Cette section sera activée dans une prochaine version.
          </EmptyState>
        )}
      </div>
    </div>
  )
}
