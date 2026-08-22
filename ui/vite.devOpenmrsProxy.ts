import type { Plugin, ViteDevServer } from 'vite'
import { request as httpsRequest } from 'node:https'
import type { IncomingMessage, ServerResponse } from 'node:http'

/**
 * Dev-only, read-only forwarder for OpenMRS patient search.
 *
 * Why this exists instead of calling OpenMRS from the browser directly:
 * SSO is off in this deployment (docs/ARCHITECTURE.md §5, SSO_ENABLED=false),
 * so the only way to reach OpenMRS today is Basic Auth with the local admin
 * credential. That credential must never reach the browser — not in a
 * fetch header, not in the bundle, not in devtools' network tab. This plugin
 * holds it in the Vite dev server's own Node process (never sent to the
 * client) and forwards exactly one whitelisted, GET-only, read-only path.
 *
 * This is explicitly NOT the production identity path. UI-001A
 * (docs/UI-001-CLINICAL-UX.md §15a) found that path UNRESOLVED — it requires
 * either SSO activation or a BFF design neither of which this ticket builds.
 * This proxy exists only so the search screen can be demonstrated against the
 * real pinned stack in development.
 *
 * It also implements §13a.1's finding in real code: a raw OpenMRS error
 * response can carry a full Java stack trace in `detail`/`rawMessage`. That
 * must never reach a browser, so every non-2xx response is rewritten down to
 * the fixed {code, message} vocabulary (§20) before it leaves this process.
 */

const WHITELISTED_PATH = '/api/openmrs/patient'

function sendJson(res: ServerResponse, status: number, body: unknown) {
  res.statusCode = status
  res.setHeader('Content-Type', 'application/json')
  res.end(JSON.stringify(body))
}

function errorBody(code: string, message: string) {
  return { error: { code, message } }
}

export function openmrsDevProxyPlugin(env: Record<string, string>): Plugin {
  const target = env.OPENMRS_DEV_PROXY_TARGET || 'https://emr.localhost:8443'
  const username = env.OPENMRS_DEV_PROXY_USERNAME || 'admin'
  const password = env.OMRS_ADMIN_PASSWORD || ''

  return {
    name: 'daher-openmrs-dev-proxy',
    configureServer(server: ViteDevServer) {
      server.middlewares.use(WHITELISTED_PATH, (req: IncomingMessage, res: ServerResponse) => {
        if (req.method !== 'GET') {
          sendJson(res, 405, errorBody('VALIDATION_ERROR', 'Méthode non autorisée.'))
          return
        }
        if (!password) {
          sendJson(
            res,
            500,
            errorBody(
              'SERVER_ERROR',
              'OMRS_ADMIN_PASSWORD est absent de .env — la recherche patient ne peut pas être démontrée.',
            ),
          )
          return
        }

        const fullUrl = new URL(req.url ?? '', 'http://localhost')
        const upstreamUrl = new URL(`/openmrs/ws/rest/v1/patient${fullUrl.search}`, target)
        const auth = Buffer.from(`${username}:${password}`).toString('base64')

        const upstreamReq = httpsRequest(
          upstreamUrl,
          {
            method: 'GET',
            headers: { Authorization: `Basic ${auth}`, Accept: 'application/json' },
            // Dev-only: Caddy's internal CA (docs/ARCHITECTURE.md §5's note on
            // local HTTPS) is not in Node's trust store. Never do this against
            // a real public endpoint.
            rejectUnauthorized: false,
          },
          (upstreamRes) => {
            const chunks: Buffer[] = []
            upstreamRes.on('data', (chunk) => chunks.push(chunk))
            upstreamRes.on('end', () => {
              const status = upstreamRes.statusCode ?? 502
              if (status >= 200 && status < 300) {
                res.statusCode = status
                res.setHeader('Content-Type', 'application/json')
                res.end(Buffer.concat(chunks))
                return
              }
              if (status === 401) {
                sendJson(
                  res,
                  401,
                  errorBody('NOT_AUTHORIZED', 'Authentification requise ou insuffisante.'),
                )
                return
              }
              // Every other failure — never forward `detail`/`rawMessage`/stack traces (§13a.1).
              sendJson(res, status, errorBody('SERVER_ERROR', 'Une erreur du serveur est survenue.'))
            })
          },
        )

        upstreamReq.on('error', () => {
          sendJson(res, 503, errorBody('NETWORK_ERROR', 'Connexion au serveur impossible.'))
        })

        upstreamReq.end()
      })
    },
  }
}
