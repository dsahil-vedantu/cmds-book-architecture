"""Smoke test for the FastAPI app — hits /api/health without any external deps."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_health() -> None:
    with TestClient(app) as client:
        r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
