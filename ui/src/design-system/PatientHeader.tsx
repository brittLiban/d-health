import type { OpenMrsPatient } from '../api/types'
import { formatBirthInfo, formatIdentifier } from './formatPatient'
import styles from './PatientHeader.module.css'

interface PatientHeaderProps {
  patient: OpenMrsPatient
}

export function PatientHeader({ patient }: PatientHeaderProps) {
  return (
    <header className={styles.header}>
      <h1 className={styles.name}>{patient.display}</h1>
      <p className={styles.meta}>{formatBirthInfo(patient)}</p>
      <p className={styles.meta}>{formatIdentifier(patient)}</p>
    </header>
  )
}
