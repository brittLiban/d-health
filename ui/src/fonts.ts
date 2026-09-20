/* Self-hosted typefaces — DH-MVP-POLISH-001.
 *
 * Imported for their side effect (each module is a stylesheet declaring
 * `@font-face`). Vite rewrites the `url()` inside them to hashed assets emitted
 * into `dist/assets`, so the built application serves every font from its own
 * origin.
 *
 * No font may be fetched from Google Fonts or any other third-party CDN at
 * runtime. Two reasons, both operational rather than aesthetic: a clinic on a
 * degraded link must not block text rendering on a host it cannot reach, and a
 * request to a font CDN would tell a third party which application is open and
 * from where.
 *
 * Only the Latin subsets and only the weights the design actually uses are
 * imported. Pulling the full family would emit Cyrillic, Greek and Vietnamese
 * faces into the bundle that this product never renders. `latin-ext` is
 * included for the sans and the serif because French needs it for the OE
 * ligature and for characters outside Latin-1 that appear in proper nouns.
 */

import '@fontsource/ibm-plex-sans/latin-400.css'
import '@fontsource/ibm-plex-sans/latin-500.css'
import '@fontsource/ibm-plex-sans/latin-600.css'
import '@fontsource/ibm-plex-sans/latin-ext-400.css'
import '@fontsource/ibm-plex-sans/latin-ext-500.css'
import '@fontsource/ibm-plex-sans/latin-ext-600.css'

import '@fontsource/source-serif-4/latin-600.css'
import '@fontsource/source-serif-4/latin-700.css'
import '@fontsource/source-serif-4/latin-ext-600.css'
import '@fontsource/source-serif-4/latin-ext-700.css'

import '@fontsource/ibm-plex-mono/latin-400.css'
import '@fontsource/ibm-plex-mono/latin-500.css'
