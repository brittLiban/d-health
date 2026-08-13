FROM python:3.13.14-slim-bookworm@sha256:67a1e1f215ccda113cfc024e8639049257e88f273898f595b61476d128d387e8 AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd --gid 10001 adapter \
    && useradd --uid 10001 --gid adapter --create-home --shell /usr/sbin/nologin adapter

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --requirement requirements.txt

COPY --chown=adapter:adapter alembic.ini entrypoint.sh ./
COPY --chown=adapter:adapter migrations ./migrations
COPY --chown=adapter:adapter src ./src

RUN mkdir -p /var/lib/daher/audit \
    && chown -R adapter:adapter /var/lib/daher /app \
    && chmod 0755 /app/entrypoint.sh

USER adapter

FROM base AS test

USER root
COPY requirements-dev.txt ./
RUN pip install --no-cache-dir --requirement requirements-dev.txt
COPY --chown=adapter:adapter pyproject.toml ./
COPY --chown=adapter:adapter tests ./tests
USER adapter
CMD ["pytest", "-q"]

FROM base AS runtime

EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
