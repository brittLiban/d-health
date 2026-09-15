import { Card } from '../../../design-system'
import { useClinic } from '../ClinicContext'

export function ClinicPatientsPage() {
  const { activeProvider } = useClinic()

  return (
    <div style={{ padding: 'var(--space-5)', maxWidth: '900px', margin: '0 auto' }}>
      <header style={{ marginBottom: 'var(--space-5)' }}>
        <h1 style={{ margin: 0, fontSize: 'var(--font-size-2xl)', color: 'var(--color-neutral-900)' }}>
          Dossiers Patients
        </h1>
        {activeProvider ? (
          <p style={{ margin: 'var(--space-1) 0 0', color: 'var(--color-neutral-600)' }}>
            Établissement prescripteur : <strong>{activeProvider.organization_name}</strong>
          </p>
        ) : null}
      </header>

      <Card>
        <div style={{ padding: 'var(--space-5)' }}>
          <h2 style={{ fontSize: 'var(--font-size-lg)', marginTop: 0, color: 'var(--color-neutral-900)' }}>
            Gestion native des patients (Cadrage DH-CLINIC-002)
          </h2>
          <p style={{ color: 'var(--color-neutral-700)', lineHeight: 1.5 }}>
            Cette section native DHealth Clinic permettra au médecin ou à la clinique de rechercher
            un patient existant ou d'enregistrer les coordonnées minimales requises pour la prescription
            d'analyses de laboratoire.
          </p>

          <div
            style={{
              backgroundColor: 'var(--color-neutral-100)',
              borderRadius: 'var(--radius-md)',
              padding: 'var(--space-4)',
              marginTop: 'var(--space-4)',
              borderLeft: '4px solid var(--color-primary-600)',
            }}
          >
            <h3 style={{ margin: '0 0 var(--space-2)', fontSize: 'var(--font-size-base)', color: 'var(--color-neutral-800)' }}>
              Principes architecturaux et données de santé
            </h3>
            <ul style={{ margin: 0, paddingLeft: 'var(--space-4)', color: 'var(--color-neutral-700)', lineHeight: 1.6 }}>
              <li>
                <strong>Périmètre délimité par établissement :</strong> Les références patients (<code>PatientReference</code>)
                sont strictement isolées par établissement de soins prescripteur.
              </li>
              <li>
                <strong>Absence de registre national centralisé :</strong> DHealth Exchange est un réseau d'échange
                diagnostique transactionnel et non un registre national de population. Aucun croisement flou ou
                fusion démographique non autorisée n'est effectué.
              </li>
              <li>
                <strong>Respect de la vie privée et minimisation :</strong> Aucune donnée clinique ou démographique superflue
                n'est transmise ou inventée.
              </li>
            </ul>
          </div>

          <div style={{ marginTop: 'var(--space-4)', color: 'var(--color-neutral-600)', fontSize: 'var(--font-size-sm)' }}>
            <em>
              Statut : Le module natif de recherche et création de patients sera activé dans le ticket{' '}
              <strong>DH-CLINIC-002</strong>, dès validation de l'API de recherche sécurisée par établissement.
            </em>
          </div>
        </div>
      </Card>
    </div>
  )
}
