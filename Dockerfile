FROM debian:13.4

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential nodejs npm python3 python3-pip ripgrep ffmpeg gcc python3-dev libffi-dev && \
    rm -rf /var/lib/apt/lists/*

COPY hermes-agent/ /opt/hermes
WORKDIR /opt/hermes

RUN pip install --no-cache-dir -e ".[messaging,cron,cli,pty,mcp,voice]" --break-system-packages && \
    npm install --prefer-offline --no-audit && \
    npx playwright install --with-deps chromium --only-shell && \
    npm cache clean --force

WORKDIR /opt/hermes
RUN chmod +x /opt/hermes/docker/entrypoint.sh

ENV HERMES_HOME=/opt/data
VOLUME [ "/opt/data" ]
ENTRYPOINT [ "/opt/hermes/docker/entrypoint.sh" ]
CMD ["gateway"]
