import '@testing-library/jest-dom/vitest'
import { afterEach, expect } from 'vitest'
import { cleanup } from '@testing-library/react'
import { toHaveNoViolations } from 'jest-axe'

expect.extend(toHaveNoViolations)

// Not automatic without vitest's `globals: true`, which this project
// deliberately avoids (explicit imports over implicit globals) — so cleanup
// is wired here instead, once, for every test file.
afterEach(() => {
  cleanup()
})
