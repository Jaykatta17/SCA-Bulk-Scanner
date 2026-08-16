# SPDX-License-Identifier: Apache-2.0
"""Unit tests for scanner.py's pure/deterministic logic.

Excludes the actual clone/Syft/Grype pipeline, which needs a live Docker
daemon and network access — not available in this sandbox. See
webapp/README.md for how to exercise run_scan() end-to-end.
"""

import os
from pathlib import Path

import pytest

from app import config, scanner


@pytest.mark.parametrize(
    "value,expected",
    [
        ("https://github.com/org/repo.git", True),
        ("main", True),
        ("", False),
        ("-rf", False),
        ("--upload-pack=touch /tmp/pwned", False),
    ],
)
def test_is_safe_arg(value, expected):
    assert scanner.is_safe_arg(value) is expected


@pytest.mark.parametrize(
    "name,expected",
    [
        ("My Project", "My_Project"),
        ("weird///chars!!", "weird_chars"),
        ("---", "project"),
        ("already_safe-name", "already_safe-name"),
    ],
)
def test_sanitize_name(name, expected):
    assert scanner.sanitize_name(name) == expected


def test_build_git_url_and_env_no_token():
    url, env, askpass = scanner._build_git_url_and_env("https://github.com/org/repo.git", None)
    assert url == "https://github.com/org/repo.git"
    assert askpass is None
    assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_build_git_url_and_env_with_token_injects_username_and_askpass():
    url, env, askpass = scanner._build_git_url_and_env(
        "https://github.com/org/repo.git", "super-secret-token"
    )
    try:
        assert url == "https://x-access-token@github.com/org/repo.git"
        assert "super-secret-token" not in url  # secret must never land in the URL/argv
        assert env["GIT_ASKPASS"] == askpass
        assert env["GIT_SCANNER_TOKEN"] == "super-secret-token"
        assert Path(askpass).exists()
        script = Path(askpass).read_text()
        assert "GIT_SCANNER_TOKEN" in script
    finally:
        os.remove(askpass)


def test_build_git_url_and_env_rejects_non_https_with_token():
    with pytest.raises(scanner.ScanError):
        scanner._build_git_url_and_env("git@github.com:org/repo.git", "token")


def test_build_git_url_and_env_preserves_existing_userinfo():
    url, _env, askpass = scanner._build_git_url_and_env(
        "https://existing-user@github.com/org/repo.git", "token"
    )
    try:
        assert url == "https://existing-user@github.com/org/repo.git"
    finally:
        os.remove(askpass)


def test_host_mount_path_translation(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    host_workspace = "/host/absolute/workspace"
    monkeypatch.setattr(config, "WORKSPACE_DIR", workspace)
    monkeypatch.setattr(config, "HOST_WORKSPACE_DIR", host_workspace)

    project_path = workspace / "myproject_ab12cd34"
    project_path.mkdir()
    assert scanner._host_mount_path(project_path) == f"{host_workspace}/myproject_ab12cd34"


def test_container_resources_uses_explicit_override(monkeypatch):
    monkeypatch.setattr(config, "CONTAINER_CPUS", "1.5")
    monkeypatch.setattr(config, "CONTAINER_MEM", "2048m")
    cpus, mem = scanner._container_resources()
    assert cpus == "1.5"
    assert mem == "2048m"


def test_container_resources_computed_from_host(monkeypatch):
    monkeypatch.setattr(config, "CONTAINER_CPUS", None)
    monkeypatch.setattr(config, "CONTAINER_MEM", "4086m")
    monkeypatch.setattr(config, "MAX_CONCURRENT_SCANS", 4)
    monkeypatch.setattr(os, "cpu_count", lambda: 8)
    cpus, mem = scanner._container_resources()
    assert cpus == "2.00"  # 8 host cpus / 4 concurrent slots
    assert mem == "4086m"


def test_container_resources_floors_at_half_cpu(monkeypatch):
    monkeypatch.setattr(config, "CONTAINER_CPUS", None)
    monkeypatch.setattr(config, "MAX_CONCURRENT_SCANS", 16)
    monkeypatch.setattr(os, "cpu_count", lambda: 4)
    cpus, _mem = scanner._container_resources()
    assert cpus == "0.50"


def test_severity_count_and_location_cleanup_regexes():
    html = (
        '<div class="severity-count" id="criticalCount">3</div>'
        '<div class="severity-count" id="highCount">0</div>'
        '<td>[Location<RealPath="/app/go.sum" AnnotatedPath="">]</td>'
    )
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0}
    for key, value in scanner._SEVERITY_COUNT_RE.findall(html):
        counts[key] = int(value)
    assert counts["critical"] == 3
    assert counts["high"] == 0

    cleaned = scanner._LOCATION_CELL_RE.sub(r"<td>\1</td>", html)
    assert cleaned.endswith("<td>/app/go.sum</td>")


def test_run_scan_rejects_unsafe_inputs():
    inputs = scanner.ScanInputs(
        scan_id="deadbeef", project_name="x", repo_url="https://x/y.git", branch="--upload-pack=evil"
    )
    with pytest.raises(scanner.ScanError):
        scanner.run_scan(inputs, lambda _status: None)
