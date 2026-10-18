"""Fixtures for app.db tests: every test gets a fresh SQLite file under tmp_path."""

import pytest

import app.db as db


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "nested" / "finally.db"
    db.init_db(path)
    return path
