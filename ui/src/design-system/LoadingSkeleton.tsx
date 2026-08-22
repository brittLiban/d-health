import styles from './LoadingSkeleton.module.css'

interface LoadingSkeletonProps {
  label?: string
  rows?: number
}

// role="status" announces the loading state to screen readers even though the
// shimmering bars themselves carry no accessible information (§19, §16).
export function LoadingSkeleton({ label = 'Chargement…', rows = 3 }: LoadingSkeletonProps) {
  return (
    <div role="status" aria-live="polite">
      <span className="visually-hidden">{label}</span>
      {Array.from({ length: rows }).map((_, index) => (
        <div
          key={index}
          className={styles.skeleton}
          style={{ marginBottom: 'var(--space-2)' }}
          aria-hidden="true"
        />
      ))}
    </div>
  )
}
