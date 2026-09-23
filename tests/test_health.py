"""Tests for /api/health."""

import time
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from api import main
from api.core.database import Database


@pytest.fixture
def database(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    db.initialize_schema()
    return db


@pytest.fixture
def client(monkeypatch, database):
    manager = Mock()
    manager.get_status.return_value = {"state": "starting"}
    monkeypatch.setattr(main, "lightshow_manager", manager)
    monkeypatch.setattr(main, "db", database)
    monkeypatch.setattr(main.app.state, "start_time", time.time() - 100, raising=False)
    # No `with` block: lifespan (DB init, scheduler, etc.) is not run
    return TestClient(main.app)


@pytest.mark.unit
class TestHealth:

    def test_reports_manager_state_and_uptime(self, client):
        data = client.get("/api/health").json()
        assert data["lightshow_state"] == "starting"
        assert data["uptime"] >= 100
        assert data["database_ok"] is True
        assert data["api_version"] == main.API_VERSION

    def test_counts_online_clients(self, client, database):
        with database.get_connection() as conn:
            conn.execute("INSERT INTO clients (id, name, auth_key, status) VALUES ('a', 'A', 'k1', 'online')")
            conn.execute("INSERT INTO clients (id, name, auth_key, status) VALUES ('b', 'B', 'k2', 'offline')")
            conn.execute("INSERT INTO clients (id, name, auth_key, status) VALUES ('c', 'C', 'k3', 'online')")
        assert client.get("/api/health").json()["clients_online"] == 2

    def test_idle_without_manager(self, client, monkeypatch):
        monkeypatch.setattr(main, "lightshow_manager", None)
        assert client.get("/api/health").json()["lightshow_state"] == "idle"
