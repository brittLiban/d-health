FROM postgres:14.4@sha256:9ceb24f8c5f15c053d973a3610866f473690875dc13eb3282b45302189321040

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       awscli ca-certificates curl default-mysql-client jq openssl \
    && rm -rf /var/lib/apt/lists/*

COPY backup.sh /usr/local/bin/daher-backup
COPY scheduler.sh /usr/local/bin/daher-backup-scheduler
RUN chmod 0755 /usr/local/bin/daher-backup /usr/local/bin/daher-backup-scheduler

ENTRYPOINT []
CMD ["/usr/local/bin/daher-backup-scheduler"]
