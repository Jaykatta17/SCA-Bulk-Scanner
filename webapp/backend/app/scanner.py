# SPDX-License-Identifier: Apache-2.0
"""Single-project clone + SBOM + vulnerability scan pipeline.

This mirrors the CSV-driven flow in clone_repos.py / scan_project.sh (same
git-argument-injection guards, same report post-processing) but drives one
project at a time on behalf of the web UI, and reports progress via a
callback instead of printing to a terminal.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit, urlunsplit

from . import config

_SEVERITY_COUNT_RE = re.compile(r'id="(critical|high|medium|low|unknown)Count">(\d+)</div>')
_LOCATION_CELL_RE = re.compile(r'<td>\[Location<RealPath="([^"]+)".*?>\]</td>')


class ScanError(Exception):
    """Expected, user-facing scan failure (bad input, clone/scan failure)."""


def is_safe_arg(value: str) -> bool:
    """Reject values that could be parsed as CLI flags (git argument injection)."""
    return bool(value) and not value.startswith("-")


def sanitize_name(name: str) -> str:
    sanitized = re.sub(r"[^a-zA-Z0-9\-_]", "_", name)
    sanitized = re.sub(r"_+", "_", sanitized)
    return sanitized.strip("_-") or "project"


@dataclass
class ScanInputs:
    scan_id: str
    project_name: str
    repo_url: str
    branch: str
    commit_id: Optional[str] = None
    git_token: Optional[str] = None


@dataclass
class ScanOutcome:
    commit_resolved: Optional[str] = None
    report_filename: Optional[str] = None
    severity_counts: dict = field(
        default_factory=lambda: {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0}
    )


def _run(cmd: list[str], cwd: Optional[Path] = None, env: Optional[dict] = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd, cwd=cwd, env=env, capture_output=True, timeout=config.SCAN_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired as exc:
        raise ScanError(f"command timed out after {config.SCAN_TIMEOUT_SECONDS}s: {' '.join(cmd[:2])}") from exc


def _build_git_url_and_env(repo_url: str, token: Optional[str]) -> tuple[str, dict, Optional[str]]:
    """Return (url, subprocess env, askpass script path to clean up afterwards).

    When a token is supplied, auth is passed via a short-lived GIT_ASKPASS
    script + env var rather than embedding it in the URL, so the secret
    never gets written into the cloned repo's .git/config or into any
    argv that a `ps aux` on the host could observe.
    """
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    if not token:
        return repo_url, env, None

    parts = urlsplit(repo_url)
    if parts.scheme not in ("http", "https"):
        raise ScanError("git_token is only supported for http(s) repository URLs")

    netloc = parts.netloc
    if "@" not in netloc:
        netloc = f"x-access-token@{netloc}"
    url_with_user = urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))

    fd, askpass_path = tempfile.mkstemp(prefix="git-askpass-", suffix=".sh")
    with os.fdopen(fd, "w") as f:
        f.write('#!/bin/sh\nexec printf \'%s\' "$GIT_SCANNER_TOKEN"\n')
    os.chmod(askpass_path, stat.S_IRWXU)

    env["GIT_ASKPASS"] = askpass_path
    env["GIT_SCANNER_TOKEN"] = token
    return url_with_user, env, askpass_path


def _host_mount_path(project_path: Path) -> str:
    """Translate a WORKSPACE_DIR-relative path to the HOST path Docker needs
    for `-v` when this process runs inside a container (see config.py)."""
    rel = project_path.relative_to(config.WORKSPACE_DIR)
    return str(Path(config.HOST_WORKSPACE_DIR) / rel)


def _container_resources() -> tuple[str, str]:
    if config.CONTAINER_CPUS:
        return config.CONTAINER_CPUS, config.CONTAINER_MEM
    host_cpus = os.cpu_count() or 4
    cpus = max(0.5, host_cpus / max(1, config.MAX_CONCURRENT_SCANS))
    return f"{cpus:.2f}", config.CONTAINER_MEM


def run_scan(inputs: ScanInputs, on_status: Callable[[str], None]) -> ScanOutcome:
    """Blocking end-to-end scan. Call from a worker thread, not the event loop."""
    for label, value in (
        ("project_name", inputs.project_name),
        ("repo_url", inputs.repo_url),
        ("branch", inputs.branch),
    ):
        if not is_safe_arg(value):
            raise ScanError(f"invalid {label}: must not be empty or start with '-'")
    if inputs.commit_id and not is_safe_arg(inputs.commit_id):
        raise ScanError("invalid commit_id: must not start with '-'")

    config.WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    project_path = config.WORKSPACE_DIR / f"{sanitize_name(inputs.project_name)}_{inputs.scan_id[:8]}"
    if project_path.exists():
        shutil.rmtree(project_path, ignore_errors=True)

    askpass_path: Optional[str] = None
    try:
        on_status("cloning")
        git_url, git_env, askpass_path = _build_git_url_and_env(inputs.repo_url, inputs.git_token)

        if inputs.commit_id:
            clone_cmd = ["git", "clone", "-b", inputs.branch, "--filter=blob:none", git_url, str(project_path)]
        else:
            clone_cmd = ["git", "clone", "-b", inputs.branch, "--depth", "1", git_url, str(project_path)]

        result = _run(clone_cmd, cwd=config.WORKSPACE_DIR, env=git_env)
        if result.returncode != 0:
            raise ScanError(f"git clone failed: {_tail(result.stderr)}")

        if inputs.commit_id:
            result = _run(["git", "checkout", inputs.commit_id], cwd=project_path, env=git_env)
            if result.returncode != 0:
                raise ScanError(f"git checkout of commit {inputs.commit_id!r} failed: {_tail(result.stderr)}")

        commit_result = _run(["git", "rev-parse", "--short", "HEAD"], cwd=project_path)
        commit_resolved = commit_result.stdout.decode(errors="replace").strip() if commit_result.returncode == 0 else None

        on_status("scanning")
        return _scan_project(inputs, project_path, commit_resolved)
    finally:
        if askpass_path:
            try:
                os.remove(askpass_path)
            except OSError:
                pass
        shutil.rmtree(project_path, ignore_errors=True)


def _scan_project(inputs: ScanInputs, project_path: Path, commit_resolved: Optional[str]) -> ScanOutcome:
    host_mount = _host_mount_path(project_path)
    cpus, mem = _container_resources()

    syft_cmd = [
        "docker", "run", "--rm", "-e", "SYFT_CHECK_FOR_APP_UPDATE=false",
        "--cpus", cpus, "--memory", mem, "--memory-swap", mem,
        "-v", f"{host_mount}:/app",
        config.SYFT_IMAGE, "/app/", "-o", "cyclonedx-json",
    ]
    result = _run(syft_cmd)
    if result.returncode != 0:
        raise ScanError(f"SBOM generation failed: {_tail(result.stderr)}")
    (project_path / "sbom.json").write_bytes(result.stdout)

    branch_safe = sanitize_name(inputs.branch)
    project_safe = sanitize_name(inputs.project_name)
    scan_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_filename = (
        f"SCA-{project_safe}-{branch_safe}_{commit_resolved or 'unknown'}-{scan_date}-{inputs.scan_id[:8]}.html"
    )

    # Written straight into project_path (already bind-mounted as /app for
    # both containers) so grype can read it without a second -v mount; using
    # plain str.replace (not sed) sidesteps the delimiter/metacharacter
    # escaping that scan_project.sh has to do for the same substitution.
    template_text = config.TEMPLATE_PATH.read_text(encoding="utf-8")
    template_text = (
        template_text.replace("PROJECT_VAR", inputs.project_name)
        .replace("BRANCH_VAR", inputs.branch)
        .replace("COMMIT_VAR", commit_resolved or "unknown")
    )
    (project_path / "html-injected.tmpl").write_text(template_text, encoding="utf-8")

    grype_cmd = [
        "docker", "run", "--rm", "-e", "GRYPE_BY_CVE=true",
        "--cpus", cpus, "--memory", mem, "--memory-swap", mem,
        "-v", f"{host_mount}:/app",
        config.GRYPE_IMAGE, "sbom:/app/sbom.json",
        "-o", "template", "-t", "/app/html-injected.tmpl",
    ]
    result = _run(grype_cmd)
    if result.returncode != 0:
        raise ScanError(f"vulnerability scan failed: {_tail(result.stderr)}")

    html = result.stdout.decode(errors="replace")
    html = _LOCATION_CELL_RE.sub(r"<td>\1</td>", html)
    (config.REPORTS_DIR / report_filename).write_text(html, encoding="utf-8")

    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0}
    for key, value in _SEVERITY_COUNT_RE.findall(html):
        counts[key] = int(value)

    return ScanOutcome(commit_resolved=commit_resolved, report_filename=report_filename, severity_counts=counts)


def _tail(data: bytes, limit: int = 2000) -> str:
    text = data.decode(errors="replace").strip()
    return text[-limit:]
