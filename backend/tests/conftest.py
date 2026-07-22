from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


TEST_DB = Path("data/test_evoagent.db")
os.environ["EVO_DATABASE_URL"] = "sqlite+aiosqlite:///./data/test_evoagent.db"
for candidate in (TEST_DB, Path(f"{TEST_DB}-shm"), Path(f"{TEST_DB}-wal")):
    candidate.unlink(missing_ok=True)

from backend.app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as value:
        yield value


def pytest_sessionfinish(session, exitstatus):
    for candidate in (TEST_DB, Path(f"{TEST_DB}-shm"), Path(f"{TEST_DB}-wal")):
        candidate.unlink(missing_ok=True)

