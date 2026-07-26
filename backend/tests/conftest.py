from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile

import pytest
from fastapi.testclient import TestClient


TEST_DB = Path("data/test_evoagent.db")
TEST_RUNTIME_ROOT = Path(tempfile.gettempdir()) / "evoagent-next-tests"
os.environ["EVO_DATABASE_URL"] = "sqlite+aiosqlite:///./data/test_evoagent.db"
os.environ["EVO_WORKSPACE_ROOT"] = str(TEST_RUNTIME_ROOT / "workspace")
os.environ["EVO_SKILLS_ROOT"] = str(TEST_RUNTIME_ROOT / "skills")
os.environ["EVO_PLUGINS_ROOT"] = str(TEST_RUNTIME_ROOT / "plugins")
# Production requires every Agent to use a configured online endpoint. The test
# suite deliberately keeps the deterministic provider available so API and
# orchestration behavior can be verified without making paid network requests.
os.environ["EVO_REQUIRE_ONLINE_AGENTS"] = "false"
shutil.rmtree(TEST_RUNTIME_ROOT, ignore_errors=True)
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
    shutil.rmtree(TEST_RUNTIME_ROOT, ignore_errors=True)
