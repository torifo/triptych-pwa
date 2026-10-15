"""Shared test setup.

The NDL series adapter reads the catalog (backend/catalog.db) for 近刊 volumes; tests must never
see the real one, so every test starts with TRIPTYCH_CATALOG_DB pointing at a file that does not
exist. Tests that need a catalog set the variable themselves (monkeypatch.setenv wins).
"""

from __future__ import annotations

import pytest

from backend.catalog import db


@pytest.fixture(scope="session")
def _missing_catalog(tmp_path_factory):
    return tmp_path_factory.mktemp("no-catalog") / "catalog.db"


@pytest.fixture(autouse=True)
def _no_real_catalog(monkeypatch, _missing_catalog):
    monkeypatch.setenv(db.DB_ENV_VAR, str(_missing_catalog))
