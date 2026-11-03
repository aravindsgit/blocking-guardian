import base64
import os
import tempfile

import pytest

# Throwaway database and known login. Must be set before the app is imported.
os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/test.db"
os.environ["ENV"] = "test"
os.environ["APP_USERNAME"] = "admin"
os.environ["APP_PASSWORD"] = "test-password-123"
os.environ.pop("ANTHROPIC_API_KEY", None)

from fastapi.testclient import TestClient

from app.main import app
from app.models import Base, engine

AUTH = {"Authorization": "Basic " + base64.b64encode(b"admin:test-password-123").decode()}


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


@pytest.fixture
def anon():
    return TestClient(app)


@pytest.fixture
def client():
    return TestClient(app, headers=AUTH)
