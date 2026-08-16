# SPDX-License-Identifier: Apache-2.0
"""API-level tests using mongomock in place of a live MongoDB.

These exercise routing, validation, and Mongo document shape — they do not
run real git/docker scans (no daemon in CI/dev sandboxes); scanner.py's pure
functions are covered separately in test_scanner.py.
"""

import mongomock
import pytest
from fastapi.testclient import TestClient

from app import db, main


@pytest.fixture(autouse=True)
def fake_mongo(monkeypatch):
    client = mongomock.MongoClient(tz_aware=True)
    monkeypatch.setattr(db, "_client", client)
    yield client
    db._client = None


@pytest.fixture(autouse=True)
def no_real_scans(monkeypatch):
    """Prevent the background worker from dequeuing scans during tests.

    Without this, whether a scan is still "queued" by the time a test makes
    its next assertion is a race against the worker task — these tests are
    about the API/DB layer, not that timing, so keep it deterministic by
    never letting the queue drain. Real scan execution (scanner.py's pure
    functions) is covered separately in test_scanner.py.
    """

    async def noop_put(_item):
        return None

    monkeypatch.setattr(main._queue, "put", noop_put)


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["mongo"] is True


def test_create_and_get_scan(client):
    payload = {
        "project_name": "Demo App",
        "repo_url": "https://github.com/example/demo.git",
        "branch": "main",
        "author": "jay",
    }
    resp = client.post("/api/scans", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert body["project_name"] == "Demo App"
    assert body["status"] == "queued"
    assert "git_token" not in body
    assert body["used_git_token"] is False

    scan_id = body["scan_id"]
    resp2 = client.get(f"/api/scans/{scan_id}")
    assert resp2.status_code == 200
    assert resp2.json()["scan_id"] == scan_id


def test_git_token_never_returned(client):
    payload = {
        "project_name": "Private Repo",
        "repo_url": "https://github.com/example/private.git",
        "branch": "main",
        "git_token": "super-secret-token",
    }
    resp = client.post("/api/scans", json=payload)
    assert resp.status_code == 201
    body = resp.json()
    assert "git_token" not in body
    assert body["used_git_token"] is True
    assert "super-secret-token" not in resp.text


@pytest.mark.parametrize(
    "payload,field",
    [
        ({"project_name": "", "repo_url": "https://x/y.git", "branch": "main"}, "project_name"),
        ({"project_name": "x", "repo_url": "not-a-url", "branch": "main"}, "repo_url"),
        ({"project_name": "x", "repo_url": "https://x/y.git", "branch": "-rf"}, "branch"),
        ({"project_name": "-x", "repo_url": "https://x/y.git", "branch": "main"}, "project_name"),
    ],
)
def test_validation_rejects_bad_input(client, payload, field):
    resp = client.post("/api/scans", json=payload)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any(field in str(d.get("loc")) for d in detail)


def test_list_scans_and_filters(client):
    for name in ("Alpha", "Beta"):
        client.post("/api/scans", json={
            "project_name": name, "repo_url": "https://github.com/example/repo.git", "branch": "main",
        })

    resp = client.get("/api/scans")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2

    resp = client.get("/api/scans", params={"q": "alp"})
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["project_name"] == "Alpha"


def test_scan_not_found(client):
    resp = client.get("/api/scans/does-not-exist")
    assert resp.status_code == 404


def test_report_not_available_before_completion(client):
    resp = client.post("/api/scans", json={
        "project_name": "Pending", "repo_url": "https://github.com/example/repo.git", "branch": "main",
    })
    scan_id = resp.json()["scan_id"]
    report_resp = client.get(f"/api/scans/{scan_id}/report")
    assert report_resp.status_code == 404
