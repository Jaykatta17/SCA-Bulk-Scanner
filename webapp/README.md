# SCA Bulk Scanner — Web UI

A small web app that sits in front of the existing Syft/Grype scanning
pipeline: submit one repository at a time from a form, watch it scan, and
browse every past scan's status, severity breakdown, and HTML report from a
history page backed by MongoDB. The CSV/bulk workflow (`scan_project.sh`,
`clone_repos.py`) is unchanged and still works standalone — this is an
additional, complementary entry point.

## Stack

- **Backend**: FastAPI + pymongo (`webapp/backend`), reusing the same
  git-argument-injection guards as `clone_repos.py` and driving Syft/Grype
  via `docker run`, same as `scan_project.sh`.
- **Frontend**: dependency-free HTML/CSS/JS (`webapp/frontend`), no build
  step, served as static files by the backend.
- **Database**: MongoDB, dockerized, one `scans` collection.

## Quick start (Docker)

```bash
# from the repository root
cp .env.example .env   # optional — defaults work as-is
docker compose up --build
```

Then open <http://localhost:8080>.

Reports land in the existing top-level `reports/` directory — the same
place `scan_project.sh` writes to — so both entry points share one report
archive.

> This repo's sandbox couldn't run this build+up step end-to-end (no Docker
> daemon available in that environment — see the validation notes below);
> the code has been syntax/unit-tested but you should do a first real run
> before relying on it.

### Requirements

Same as the CLI tool: Docker, and network access to clone target
repositories and pull the `anchore/syft` / `anchore/grype` images.

## How a scan runs

1. You submit the form → a `queued` document is written to MongoDB and the
   request returns immediately with a `scan_id`.
2. A small in-process worker pool (`MAX_CONCURRENT_SCANS`, default 2) picks
   it up, clones the repo (shallow if no commit given, blobless + checkout
   if one is), runs Syft for the SBOM, then Grype against
   `template/html.tmpl` — identical output to the CLI tool.
3. The history page polls while any scan is non-terminal and updates status
   badges live; a finished scan gets a "View report" link straight to the
   generated HTML file.
4. The cloned working copy is deleted after each scan (success or failure);
   only the report and the Mongo record persist.

## Git token handling

The optional **Git Token** field is for cloning private HTTPS repositories.
It is:

- used only in-memory for that one `git clone`, via a short-lived
  `GIT_ASKPASS` script + env var (never embedded in the repo URL, so it
  never ends up written into the cloned repo's `.git/config`, and never
  appears as a CLI argument another process on the host could read via
  `ps aux`);
- **never written to MongoDB** — only a `used_git_token: true/false` flag is
  stored, for audit purposes;
- never echoed back by the API (stripped server-side even if it were
  present in a raw document).

Token auth only applies to `http(s)://` URLs; it's rejected up front for
`ssh://`/`git@` URLs rather than silently ignored.

## Security notes

- **The `app` container is bind-mounted onto the host's Docker socket**
  (`/var/run/docker.sock`) so it can run Syft/Grype exactly like
  `scan_project.sh` does directly on a host — this is required for the
  "docker-outside-of-docker" pattern and is equivalent to root access on
  the host. Only run this stack on a trusted network.
- **There is no authentication layer.** Anyone who can reach the web UI can
  queue a scan of any repository it can reach, view all scan history, and
  download all reports. Put it behind a reverse proxy with auth (or a VPN)
  before exposing it beyond localhost.
- Repository URL/branch/commit inputs are validated both client- and
  server-side to reject values that look like CLI flags (the same
  argument-injection class of bug fixed in `clone_repos.py`).

## Configuration

All via environment variables (see `.env.example` for the ones exposed
through `docker-compose.yml`); full list in `webapp/backend/app/config.py`.

| Variable | Default | Purpose |
|---|---|---|
| `MONGO_URI` | `mongodb://localhost:27017` | Mongo connection string |
| `MONGO_DB` | `sca_scanner` | Database name |
| `MAX_CONCURRENT_SCANS` | `2` | Worker pool size; also used to size per-container CPU limits |
| `CONTAINER_CPUS` / `CONTAINER_MEM` | computed / `4086m` | Override the dynamic per-scan Syft/Grype container limits |
| `SCAN_TIMEOUT_SECONDS` | `1800` | Per-command timeout for clone/Syft/Grype |
| `WORKSPACE_DIR` / `REPORTS_DIR` / `TEMPLATE_PATH` | see `config.py` | Filesystem locations |
| `HOST_WORKSPACE_DIR` | = `WORKSPACE_DIR` | **Docker-outside-of-Docker path translation** — see the comment in `config.py`. Required whenever the backend itself runs in a container and talks to the host's Docker daemon; `docker-compose.yml` sets this for you. |

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/scans` | Queue a scan. Body: `project_name`, `repo_url`, `branch`, `author?`, `assessment_type?`, `commit_id?`, `git_token?`. |
| `GET` | `/api/scans` | List history. Query: `limit`, `skip`, `status`, `q` (project name search). |
| `GET` | `/api/scans/{scan_id}` | Single scan status/detail. |
| `GET` | `/api/scans/{scan_id}/report` | The generated HTML report (once `report_available` is true). |
| `GET` | `/api/health` | Liveness + Mongo connectivity. |

## Local development (without Docker)

```bash
cd webapp/backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# serve the frontend from the same process
ln -s ../../frontend app/static

# point at a local/dockerized mongod, e.g.:
docker run --rm -p 27017:27017 mongo:7
export MONGO_URI=mongodb://localhost:27017
export TEMPLATE_PATH=../../template/html.tmpl

uvicorn app.main:app --reload --port 8080
```

## Tests

```bash
cd webapp/backend
pip install -r requirements.txt mongomock httpx pytest
pytest
```

`tests/test_scanner.py` covers the pure logic (input validation, the
git-token/ASKPASS handling, the DooD host-path translation, dynamic
CPU/memory sizing, severity-count parsing) without touching Docker or git.
`tests/test_api.py` exercises the FastAPI routes against `mongomock` in
place of a real MongoDB. Neither suite runs a real clone/Syft/Grype scan —
that needs a live Docker daemon and network access, which this repo's own
sandbox didn't have available to verify against; do a real `docker compose
up` run and submit a test scan before trusting this in production.
