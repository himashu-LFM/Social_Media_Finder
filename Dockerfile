# Container image for the Curator AI API — Amazon ECS Express Mode.
#
# App Runner could build straight from the repo; ECS needs an image, which is
# the one extra step in the migration. Everything else is the same code and the
# same environment variables.
#
# NOTHING SECRET IS BAKED IN. DATABASE_URL, CORS_ORIGINS and the API keys are
# supplied as environment variables by the ECS task definition, ideally as
# references to AWS Secrets Manager. An image is not a safe place for a secret:
# anyone who can pull it can read every layer.

FROM python:3.11-slim

# Faster, quieter, and no stale .pyc files in the layer. PYTHONUNBUFFERED is
# what makes the app's print() output reach CloudWatch as it happens rather
# than sitting in a buffer until the process exits — which matters because the
# startup config check reports failures with print().
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Requirements first, as their own layer: application code changes on every
# deploy, dependencies rarely, so this keeps rebuilds to seconds.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run unprivileged. Nothing here needs root, and a container that cannot write
# outside its own workspace is one less thing to worry about.
RUN useradd --create-home --uid 10001 curator \
    && mkdir -p /app/uploads /app/exports \
    && chown -R curator:curator /app
USER curator

# ECS Express Mode routes to this port.
EXPOSE 8080

# The load balancer needs an endpoint that answers without credentials.
# /api/health is public by design and reports nothing about the deployment.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; \
        sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4).status == 200 else 1)"

# 0.0.0.0, not 127.0.0.1: the health check and the load balancer both reach the
# container from outside it, and a loopback-only bind fails every check with
# nothing useful in the logs.
#
# ONE worker, deliberately. Job progress lives in a per-process dict and each
# run continues in that process's background threads. A second worker would
# answer status polls for jobs it knows nothing about, and the UI would flicker
# between "running" and "not found". Scale the task size, not the workers.
CMD ["uvicorn", "api_server:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
