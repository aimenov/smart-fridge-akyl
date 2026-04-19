"""Pytest configuration: test DB and env must be set before importing the app."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

_tmp_root = tempfile.mkdtemp(prefix="smart-fridge-test-")
_root_path = Path(_tmp_root)

os.environ.setdefault("SMART_FRIDGE_DATABASE_URL", f"sqlite:///{_root_path.joinpath('test.db').as_posix()}")
os.environ.setdefault("SMART_FRIDGE_SCHEDULER_ENABLED", "false")
os.environ.setdefault("SMART_FRIDGE_SCAN_STORAGE", str(_root_path / "scans"))
os.environ.setdefault("SMART_FRIDGE_UPLOADS_STORAGE", str(_root_path / "uploads"))
os.environ.setdefault("SMART_FRIDGE_LOG_LEVEL", "WARNING")
os.environ.setdefault("SMART_FRIDGE_VLM_ENABLED", "false")

from fastapi.testclient import TestClient

from backend.app.database import SessionLocal, init_db
from backend.app.main import app
from backend.app.models.entities import AppSetting, Item, Product, ScanRecord


@pytest.fixture(scope="session", autouse=True)
def _init_db_once() -> None:
    init_db()


@pytest.fixture(autouse=True)
def _clean_tables() -> Generator[None, None, None]:
    db = SessionLocal()
    try:
        db.query(ScanRecord).delete()
        db.query(Item).delete()
        db.query(Product).delete()
        db.query(AppSetting).delete()
        db.commit()
    finally:
        db.close()
    yield


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture
def db_session() -> Generator:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def make_test_jpeg_bytes(*, text: str = "EXP 2030-12-31") -> bytes:
    import cv2
    import numpy as np

    img = np.ones((120, 320, 3), dtype=np.uint8) * 40
    cv2.putText(img, text, (8, 72), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (240, 240, 240), 2)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()
