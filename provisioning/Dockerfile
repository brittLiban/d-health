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
COPY requirements-dev.txt ./
RUN pip install --no-cache-dir --requirement requirements-dev.txt
COPY --chown=provisioning:provisioning pyproject.toml ./
COPY --chown=provisioning:provisioning tests ./tests
USER provisioning
CMD ["pytest", "-q"]
