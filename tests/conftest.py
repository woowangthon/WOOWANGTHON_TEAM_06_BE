import os

import pytest

os.environ["LLM_PROVIDER"] = "fake"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    from fastapi.testclient import TestClient

    from app.main import app
    return TestClient(app)


@pytest.fixture
def cohorts(client):
    a = client.post("/api/cohorts", json={"name": "2025기", "year": 2025}).json()["id"]
    b = client.post("/api/cohorts", json={"name": "2026기", "year": 2026}).json()["id"]
    return a, b


def rec(client, cohort, **kw):
    body = {"cohort_id": cohort, "type": "meeting", "title": "회의", "body": "2025-11-04 체육대회를 진행했다. 간식이 부족했다.",
            "event_name": "가을 체육대회", "occurred_on": "2025-11-04", **kw}
    return client.post("/api/records", json=body)
