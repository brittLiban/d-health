FROM python:3.13.14-slim-bookworm@sha256:67a1e1f215ccda113cfc024e8639049257e88f273898f595b61476d128d387e8 AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd --gid 10002 provisioning \
    && useradd --uid 10002 --gid provisioning --create-home --shell /usr/sbin/nologin provisioning

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --requirement requirements.txt
COPY --chown=provisioning:provisioning src ./src
USER provisioning

FROM base AS test
USER root
# The demonstration's agent invokes `docker compose` against a mounted daemon socket, so
# the CLI must exist. Production agents get it from the host, not from this image.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       ca-certificates curl gnupg \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/debian bookworm stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

USER root
COPY requirements-dev.txt ./
RUN pip install --no-cache-dir --requirement requirements-dev.txt
COPY --chown=provisioning:provisioning pyproject.toml ./
COPY --chown=provisioning:provisioning tests ./tests
USER provisioning
CMD ["pytest", "-q"]
