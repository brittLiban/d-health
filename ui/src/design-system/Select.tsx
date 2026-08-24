import { useId, type SelectHTMLAttributes } from 'react'
import styles from './TextInput.module.css'

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label: string
  error?: string
}

// Mirrors TextInput's labeling/error-wiring exactly (§19) — a native
// <select>, not a custom widget, so keyboard and screen-reader behavior are
// the platform's own rather than something this app has to reimplement.
export function Select({ label, error, id, children, ...rest }: SelectProps) {
  const generatedId = useId()
  const inputId = id ?? generatedId
  const errorId = `${inputId}-error`

  return (
    <div className={styles.field}>
      <label className={styles.label} htmlFor={inputId}>
        {label}
      </label>
      <select
        id={inputId}
        className={styles.input}
        aria-describedby={error ? errorId : undefined}
        aria-invalid={error ? true : undefined}
        {...rest}
      >
        {children}
      </select>
      {error ? (
        <span id={errorId} className={styles.error} role="alert">
          {error}
        </span>
      ) : null}
    </div>
  )
}
