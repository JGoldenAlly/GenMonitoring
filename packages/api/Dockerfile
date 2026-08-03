# Build context is the REPO ROOT (not packages/api) so this image can also
# bundle packages/agent/genmon_agent.py for GET /devices/agent/download --
# see docker-compose.yml's `api.build.context: .` / `dockerfile:
# packages/api/Dockerfile`, and the Unraid image build workflow, which must
# do the same.
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY packages/api/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY packages/api/app ./app
COPY packages/api/alembic ./alembic
COPY packages/api/alembic.ini ./alembic.ini

# Bundled so GET /devices/agent/download can serve it directly -- keep this
# in lockstep with packages/agent/genmon_agent.py's own AGENT_VERSION when
# cutting a release (see the agent's self-update mechanism).
COPY packages/agent/genmon_agent.py ./agent/genmon_agent.py

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
