from fastapi.testclient import TestClient

from agent.api.main import app


def test_health_ok() -> None:
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


def test_root_ok() -> None:
    with TestClient(app) as client:
        r = client.get("/")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
