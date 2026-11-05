from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from app.main import app


def test_health_returns_503_when_database_is_unavailable(monkeypatch):
    def fail_connect():
        raise OperationalError("SELECT 1", {}, Exception("database unavailable"))
    monkeypatch.setattr("app.main.engine.connect", fail_connect)
    response = TestClient(app).get("/health")
    assert response.status_code == 503
    assert response.json()["detail"] == {"status": "degraded", "database": "unavailable"}
