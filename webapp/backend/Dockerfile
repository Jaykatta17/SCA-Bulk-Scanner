# SPDX-License-Identifier: Apache-2.0
FROM python:3.12-slim

# docker.io provides the `docker` CLI used to drive the *host's* Docker
# daemon via the socket bind-mounted at runtime (docker-outside-of-docker,
# see docker-compose.yml and app/config.py) so this container can invoke
# `docker run` for Syft/Grype exactly like scan_project.sh does on a bare
# host. git is needed to clone target repositories.
RUN apt-get update \
    && apt-get install -y --no-install-recommends docker.io git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY frontend ./app/static

ENV PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
