# SPDX-License-Identifier: Apache-2.0
"""Environment-driven configuration for the SCA Bulk Scanner web backend."""

import os
from pathlib import Path

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.environ.get("MONGO_DB", "sca_scanner")

WORKSPACE_DIR = Path(os.environ.get("WORKSPACE_DIR", "./workspace")).resolve()
REPORTS_DIR = Path(os.environ.get("REPORTS_DIR", "./reports")).resolve()

# --- Docker-outside-of-Docker (DooD) note ---
# This backend runs as a container that talks to the *host's* Docker daemon
# over a bind-mounted /var/run/docker.sock (see docker-compose.yml) so it can
# invoke `docker run` for Syft/Grype exactly like scan_project.sh does.
# Any `-v host_path:container_path` flag we pass to `docker run` is resolved
# by that HOST daemon against the HOST filesystem — it has no idea this
# process itself is inside a container. So the workspace directory we clone
# repos into must be reachable at the SAME path on the host, and that host
# path (not our own container-internal WORKSPACE_DIR) is what belongs in
# `docker run -v ...` flags. HOST_WORKSPACE_DIR defaults to WORKSPACE_DIR so
# local (non-containerized) runs — where "this process" and "the Docker
# daemon" already share one filesystem — work unchanged.
HOST_WORKSPACE_DIR = os.environ.get("HOST_WORKSPACE_DIR", str(WORKSPACE_DIR))

TEMPLATE_PATH = Path(os.environ.get("TEMPLATE_PATH", "./template/html.tmpl")).resolve()

SYFT_IMAGE = os.environ.get("SYFT_IMAGE", "anchore/syft:latest")
GRYPE_IMAGE = os.environ.get("GRYPE_IMAGE", "anchore/grype:latest")

MAX_CONCURRENT_SCANS = int(os.environ.get("MAX_CONCURRENT_SCANS", "2"))
# Per-container CPU/memory limits, sized from host capacity unless overridden,
# same reasoning as the dynamic sizing fix applied to scan_project.sh.
CONTAINER_CPUS = os.environ.get("CONTAINER_CPUS")
CONTAINER_MEM = os.environ.get("CONTAINER_MEM", "4086m")

SCAN_TIMEOUT_SECONDS = int(os.environ.get("SCAN_TIMEOUT_SECONDS", str(30 * 60)))
