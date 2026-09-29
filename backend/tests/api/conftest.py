import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# Configure BEFORE app import: Settings is lru_cached at import time.
os.environ.setdefault("DATABASE_URL", "sqlite:///" + tempfile.mktemp(suffix=".db"))
os.environ.setdefault("STORAGE_DRIVER", "local")
os.environ.setdefault("STORAGE_LOCAL_ROOT", tempfile.mkdtemp(prefix="pf_store_"))
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "true")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("LOG_JSON", "false")


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:      # the `with` triggers lifespan -> init_db()
        yield c


@pytest.fixture(scope="session")
def other_client():
    """A second, independent browser session — used to prove cross-session isolation."""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        c.cookies.clear()
        yield c


@pytest.fixture
def isolated_client():
    """A throwaway session for destructive tests (e.g. 'clear my data')."""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        c.cookies.clear()
        yield c
