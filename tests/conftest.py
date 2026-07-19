from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stackchan_control.app import create_app
from stackchan_control.settings import PROJECT_ROOT, Settings


@pytest.fixture
def client(tmp_path: Path):
    settings = Settings(
        db_path=tmp_path / "test.db",
        seed_character_dir=PROJECT_ROOT / "config" / "seed_character",
        web_dir=PROJECT_ROOT / "web",
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def protected_client(tmp_path: Path):
    settings = Settings(
        db_path=tmp_path / "protected.db",
        seed_character_dir=PROJECT_ROOT / "config" / "seed_character",
        web_dir=PROJECT_ROOT / "web",
        admin_api_key="test-secret",
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def gateway_client(tmp_path: Path):
    settings = Settings(
        db_path=tmp_path / "gateway.db",
        seed_character_dir=PROJECT_ROOT / "config" / "seed_character",
        web_dir=PROJECT_ROOT / "web",
        admin_api_key="admin-secret",
        device_api_key="device-secret",
        gateway_heartbeat_seconds=0.03,
        gateway_timeout_seconds=0.2,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client
