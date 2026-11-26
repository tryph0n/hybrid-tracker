"""Shared paths, recorded payloads and an isolated data directory for the tests."""

import io
import re
import shutil
import zipfile
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


GEONAMES_PLACES = [
    {"id": 1, "name": "Arna", "country": "NO", "lat": "60.42", "lon": "5.46"},
    {"id": 2, "name": "Kirkenes", "country": "NO", "lat": "69.73", "lon": "30.05"},
]


def geonames_archive(places: list[dict[str, str | int]] | None = None) -> bytes:
    """The bytes of a small `cities500.zip`, one tab-separated line per place.

    Each place is a dict with `id`, `name`, `country`, `lat`, `lon` and
    optionally `ascii`, `alternates`, `code` (default `PPL`) and `population`.
    The file has 19 fields and no header, as GeoNames serves it.
    """
    lines = []
    for place in GEONAMES_PLACES if places is None else places:
        fields = [""] * 19
        fields[0] = str(place["id"])
        fields[1] = str(place["name"])
        fields[2] = str(place.get("ascii", place["name"]))
        fields[3] = str(place.get("alternates", ""))
        fields[4] = str(place["lat"])
        fields[5] = str(place["lon"])
        fields[6] = "P"
        fields[7] = str(place.get("code", "PPL"))
        fields[8] = str(place["country"])
        fields[14] = str(place.get("population", 1000))
        lines.append("\t".join(fields))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("cities500.txt", "\n".join(lines) + "\n")
    return buffer.getvalue()
