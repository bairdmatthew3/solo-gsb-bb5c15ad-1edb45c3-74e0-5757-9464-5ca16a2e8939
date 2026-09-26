"""Shared pytest fixtures.

Environment defaults are set *before* ``app.main`` is imported anywhere so
that the module-level ASGI app never touches the real working directory.
"""
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="cidr-pool-tests-")
os.environ.setdefault("DB_PATH", os.path.join(_TMP, "default.db"))
os.environ.setdefault("POOL_PARENT", "10.0.0.0/16")
os.environ.setdefault("POOL_RESERVED", "10.0.0.0/24")

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A fresh app instance backed by an empty database per test."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "pool.db"))
    monkeypatch.setenv("POOL_PARENT", "10.0.0.0/16")
    monkeypatch.setenv("POOL_RESERVED", "10.0.0.0/24")
    with TestClient(create_app()) as test_client:
        yield test_client
