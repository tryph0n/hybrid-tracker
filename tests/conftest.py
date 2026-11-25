"""Shared paths, recorded payloads and an isolated data directory for the tests."""

import io
import re
import shutil
from datetime import datetime
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

from tracker import sources

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
APP = ROOT / "src" / "streamlit_app.py"
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
LEIDEN_LINK = "https://example.org/report"


def workbook_bytes(raw: pd.DataFrame, links: dict[int, str] | None = None) -> bytes:
    """Write `raw` as the bytes of an xlsx file, as the Leiden payload is.

    Cells that look like a date become real dates. `links` maps a row position
    to the hyperlink target of its "What" cell.
    """
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "EventData"
    sheet.append(list(raw.columns))
    for position, row in enumerate(raw.itertuples(index=False)):
        sheet.append(
            [
                datetime.strptime(cell, "%Y-%m-%d")
                if isinstance(cell, str) and DATE.fullmatch(cell)
                else None
                if pd.isna(cell)
                else cell
                for cell in row
            ]
        )
        if links and position in links:
            sheet.cell(
                row=position + 2, column=raw.columns.get_loc("What") + 1
            ).hyperlink = links[position]
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def recorded_payloads(name: str) -> dict[str, bytes]:
    """The recorded upstream files of a source, by payload name."""
    if name == "eodi":
        return {
            "incidentes.csv": (FIXTURES / "eodi_incidentes.csv").read_bytes(),
            "incidentes_sin_ubicacion.json": (
                FIXTURES / "eodi_sin_ubicacion.json"
            ).read_bytes(),
        }
    if name == "leiden":
        raw = pd.read_csv(FIXTURES / "leiden_eventdata.csv")
        return {"eventdata.xlsx": workbook_bytes(raw, {1: LEIDEN_LINK})}
    raise KeyError(name)


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
