"""Shared paths and an isolated data directory for the tests."""

import shutil
from pathlib import Path

import pytest

from tracker import sources

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
APP = ROOT / "src" / "streamlit_app.py"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Point tracker.sources at an empty temporary data directory."""
    monkeypatch.setattr(sources, "DATA_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def sample_data_dir(data_dir):
    """Data directory holding the small stored-format samples of both sources."""
    for path in (FIXTURES / "events").glob("*.csv"):
        shutil.copy(path, data_dir / path.name)
    return data_dir
