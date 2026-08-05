# Autarch — self-contained, runs anywhere. Pure Python + SQLite; AES-GCM optional.
FROM python:3.12-slim

# Non-root user for safer runtime.
RUN useradd --create-home --uid 10001 autarch
WORKDIR /app

# Install the package (with crypto extra for AES-GCM at rest + provenance).
COPY pyproject.toml README.md ./
COPY autarch ./autarch
RUN pip install --no-cache-dir ".[crypto]"

# A writable workspace for the ledger, identity, and runs.
ENV AUTARCH_WORKSPACE=/data
RUN mkdir -p /data && chown -R autarch:autarch /data /app
USER autarch
VOLUME ["/data"]

# Container health probe: non-zero exit if the ledger is broken.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD ["python", "-m", "autarch", "--workspace", "/data", "health"]

# Default: serve this node's ledger to the mesh over HTTP.
EXPOSE 8787
ENTRYPOINT ["python", "-m", "autarch", "--workspace", "/data"]
CMD ["mesh", "serve", "--host", "0.0.0.0", "--port", "8787"]
