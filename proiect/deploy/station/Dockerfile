# A simulated cluster station (FR-CLUST-02, NFR-SEC-02): SSH with key authentication only.
FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends openssh-server python3 procps \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /run/sshd \
    && useradd -m -s /bin/bash maintenance \
    && usermod -p '*' maintenance \
    && mkdir -p /home/maintenance/.ssh && chmod 700 /home/maintenance/.ssh && chown maintenance:maintenance /home/maintenance/.ssh \
    && printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin no\nPubkeyAuthentication yes\n' \
       > /etc/ssh/sshd_config.d/10-keys-only.conf
COPY --chmod=755 entrypoint.sh /entrypoint.sh
EXPOSE 22
ENTRYPOINT ["/entrypoint.sh"]
