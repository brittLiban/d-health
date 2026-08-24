import type { ErrorCode } from '../api/types'
import { ERROR_MESSAGES } from '../api/errorMessages'
import { Button } from './Button'
import styles from './ErrorState.module.css'

interface ErrorStateProps {
  code: ErrorCode
  onRetry?: () => void
}

// role="alert" so an unexpected failure is announced immediately, not only
// visible to a sighted user watching the screen (§19).
export function ErrorState({ code, onRetry }: ErrorStateProps) {
  const { title, message } = ERROR_MESSAGES[code]
  return (
    <div className={styles.state} role="alert">
      <p style={{ fontWeight: 600, margin: 0 }}>{title}</p>
      <p style={{ margin: 0 }}>{message}</p>
      {onRetry ? (
        <Button type="button" variant="secondary" onClick={onRetry}>
          Réessayer
        </Button>
      ) : null}
    </div>
  )
}
