import type { OpenMrsPatient } from '../api/types'
import { Card } from './Card'
import { Button } from './Button'
import { formatBirthInfo, formatIdentifier } from './formatPatient'
import styles from './PatientCard.module.css'

interface PatientCardProps {
  patient: OpenMrsPatient
  onOpen: (patient: OpenMrsPatient) => void
}

export function PatientCard({ patient, onOpen }: PatientCardProps) {
  return (
    <Card>
      <p className={styles.name}>{patient.display}</p>
      <p className={styles.detail}>{formatBirthInfo(patient)}</p>
      <p className={styles.detail}>{formatIdentifier(patient)}</p>
      <div className={styles.actions}>
        <Button onClick={() => onOpen(patient)}>Ouvrir le dossier</Button>
      </div>
    </Card>
  )
}
