# Daher Health clinical UI — reproducible static build.
#
# This container ships the static production bundle only. It does not include
# the dev-only OpenMRS proxy (vite.devOpenmrsProxy.ts) — that Node process,
# and the credential it holds, never exists in this image. This build is
# therefore not wired to reach OpenMRS at all yet; see ui/README.md's
# "Next integration step" for what production identity/routing needs before
# that changes (docs/UI-001-CLINICAL-UX.md §15a, UNRESOLVED pending SSO).

FROM node:22-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10 AS runtime
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
