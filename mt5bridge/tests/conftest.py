import os

from cryptography.fernet import Fernet

os.environ["MT5BRIDGE_DATABASE_URL"] = "sqlite:///:memory:"
os.environ["MT5BRIDGE_SECRET_KEY"] = "test-secret"
os.environ["MT5BRIDGE_FERNET_KEY"] = Fernet.generate_key().decode()
os.environ["MT5BRIDGE_WORKER_KEY"] = "test-worker-key"
os.environ["MT5BRIDGE_ALERTS_PER_MINUTE"] = "1000"

import pytest
from fastapi.testclient import TestClient

from app.db import Base, engine
from app.main import app


@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def worker_headers():
    return {"X-Worker-Key": "test-worker-key"}
