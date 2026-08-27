import type { OpenMrsPatient } from '../../api/types'
import { Button, Card, StatusBadge } from '../../design-system'
import { formatIdentifier } from '../../design-system/formatPatient'
import { formatAdmissionDateTime } from './formatAdmissionDateTime'
import type { MaternityBoardEntry } from './deriveMaternityBoard'
import styles from './MaternityPatientCard.module.css'

interface MaternityPatientCardProps {
  entry: MaternityBoardEntry
  onOpen: (patient: OpenMrsPatient) => void
}

// Deliberately reuses PatientSearchPage's Card/Button/formatIdentifier
// primitives rather than a second visual language (§4). Deliberately omits
// PatientCard's birth-info line: measured live (PROJECT_STATE.md's UI-014
// entry) that neither this query's embedded `patient` nor any `GET
// /patient/{uuid}` representation this ticket tried ever carries `person`
// data on this pinned build — showing "non renseignée" here would
// misattribute a fetch-shape limitation to a data gap. Never renders a
// UUID, visit id, or backend-internal term (§6).
export function MaternityPatientCard({ entry, onOpen }: MaternityPatientCardProps) {
  return (
    <Card>
      <p className={styles.name}>{entry.patient.display}</p>
      <p className={styles.detail}>{formatIdentifier(entry.patient)}</p>
      <p className={styles.detail}>Admise le {formatAdmissionDateTime(entry.admittedAt)}</p>
      {entry.status === 'AMBIGUOUS' ? (
        <p role="alert" className={styles.ambiguous}>
          <StatusBadge label="État à vérifier" tone="warning" /> Plusieurs admissions actives ont été
          trouvées pour cette patiente. Ouvrez son dossier pour vérifier.
        </p>
      ) : null}
      <div className={styles.actions}>
        <Button onClick={() => onOpen(entry.patient)}>Ouvrir le dossier</Button>
      </div>
    </Card>
  )
}
