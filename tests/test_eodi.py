"""The EODI adapter on recorded samples of the two upstream files."""

import io
import json

import pandas as pd
import pytest
from conftest import FIXTURES

from tracker import eodi
from tracker.schema import validate

LOCATED = FIXTURES / "eodi_incidentes.csv"
UNLOCATED = FIXTURES / "eodi_sin_ubicacion.json"


def serve_unlocated(monkeypatch, payload: bytes) -> None:
    """Make the unlocated-incidents download return `payload`."""
    monkeypatch.setattr(
        eodi.urllib.request, "urlopen", lambda request, timeout: io.BytesIO(payload)
    )


@pytest.fixture
def events(monkeypatch) -> pd.DataFrame:
    serve_unlocated(monkeypatch, UNLOCATED.read_bytes())
    located = pd.read_csv(LOCATED)
    return eodi.normalize(
        pd.concat([located, eodi.fetch_unlocated()], ignore_index=True)
    )


def test_normalize_follows_schema(events):
    validate(events)
    assert len(events) == 9 + 3
    assert (events["source"] == "eodi").all()


@pytest.mark.parametrize(
    ("native_id", "expected"),
    [
        (
            "EODI-2024-00003",
            {
                "date": "2024-12-23",
                "countries": "NL",
                "place": "Vught, Netherlands",
                "lat": 51.65333,
                "lon": 5.2875,
                "category": "drone_overflight",
                "subcategory": "sobrevuelo",
                "event_status": "confirmed",
                "domain": "air",
                "end_date": pd.NA,
                "geo_precision": "exact",
                "actors": pd.NA,
                "actor_name": pd.NA,
                "attribution": "none",
                "attributed_by": pd.NA,
            },
        ),
        (
            "EODI-2025-00233",
            {
                "actors": "RU",
                "attribution": "official",
                "attributed_by": "Kancelaria Prezesa Rady Ministrów",
                "countries": "PL",
            },
        ),
        (
            "EODI-2026-00140",
            {
                "actors": "UA",
                "attribution": "official",
                "attributed_by": "Nacionālie bruņotie spēki",
                "category": "airspace_incursion",
            },
        ),
        ("EODI-2026-00148", {"event_status": "unconfirmed", "date": "2026-09-26"}),
        (
            "EODI-2025-00042",
            {
                "event_status": "unconfirmed",
                "date": "2025-09-22",
                "date_precision": "day",
            },
        ),
        ("EODI-2025-00112", {"event_status": "dismissed", "date": "2025-10-04"}),
        (
            "EODI-2025-00140",
            {
                "countries": "NO",
                "place": "Arna, Norway",
                "title": (
                    "Residents report several possible drone sightings "
                    "over Arna, Norway, to police"
                ),
                "category": "drone_overflight",
                "event_status": "unconfirmed",
                "attribution": "none",
                "geo_precision": "place",
                "end_date": pd.NA,
                "url": "https://droneobservatory.eu/EODI-2025-00140",
            },
        ),
        (
            "EODI-2025-00222",
            {
                "date": "2025-02-02",
                "end_date": "2025-02-02",
                "countries": "MD",
                "geo_precision": "country",
            },
        ),
        ("EODI-2024-00003", {"date_precision": "day"}),
        ("EODI-2026-00148", {"date_precision": "day"}),
        (
            "EODI-2025-00010",
            {"date_precision": "approximate", "date": "2025-09-13", "countries": "PL"},
        ),
    ],
)
def test_normalize_maps_values(events, native_id, expected):
    row = events.set_index("event_id").loc[f"eodi:{native_id}"]
    actual = row[list(expected)].to_dict()
    assert actual.keys() == expected.keys()
    for column, value in expected.items():
        if value is pd.NA:
            assert pd.isna(actual[column]), column
        else:
            assert actual[column] == value, column


def test_located_and_unlocated_coordinates(events):
    unlocated_ids = {
        "eodi:EODI-2025-00140",
        "eodi:EODI-2025-00212",
        "eodi:EODI-2025-00222",
    }
    unlocated = events["event_id"].isin(unlocated_ids)
    assert unlocated.sum() == 3
    assert events.loc[unlocated, ["lat", "lon"]].isna().all().all()
    assert events.loc[~unlocated, ["lat", "lon"]].notna().all().all()


def test_unknown_actor_stops_the_ingestion():
    located = pd.read_csv(LOCATED)
    located.loc[0, "atribucion_actor"] = "Narnia"
    with pytest.raises(ValueError, match="ACTOR_CODES"):
        eodi.normalize(located)


@pytest.mark.parametrize(
    ("name", "code"),
    [("Federacja Rosyjska", "RU"), ("Belarus", "BY"), ("Ucraina", "UA")],
)
def test_actor_name_maps_to_an_iso_code(name, code):
    located = pd.read_csv(LOCATED)
    located.loc[0, "atribucion_actor"] = name
    located.loc[0, "atribucion_autoridad"] = "Some office"
    row = eodi.normalize(located).loc[0]
    assert (row["actors"], row["attribution"], row["attributed_by"]) == (
        code,
        "official",
        "Some office",
    )


def test_unknown_nivel_stops_the_ingestion():
    located = pd.read_csv(LOCATED)
    located.loc[0, ["latitud", "longitud"]] = pd.NA
    located["nivel"] = "barrio"
    with pytest.raises(ValueError, match="GEO_PRECISION"):
        eodi.normalize(located)


def test_unknown_date_precision_stops_the_ingestion():
    located = pd.read_csv(LOCATED)
    located.loc[0, "inicio_precision"] = "semana"
    with pytest.raises(ValueError, match="DATE_PRECISION"):
        eodi.normalize(located)


def test_attributed_unlocated_incident_stops_the_ingestion(monkeypatch):
    payload = json.loads(UNLOCATED.read_text(encoding="utf-8"))
    payload["incidentes"][1]["estado"]["actual"] = "atribuido"
    serve_unlocated(monkeypatch, json.dumps(payload).encode())
    with pytest.raises(ValueError, match="Attributed unlocated incident"):
        eodi.fetch_unlocated()


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("tipo", "globo", "without category"),
        ("presencia_dron", "probable", "without event_status"),
    ],
)
def test_unknown_upstream_label_fails_validation(column, value, message):
    located = pd.read_csv(LOCATED)
    located.loc[0, column] = value
    with pytest.raises(ValueError, match=message):
        validate(eodi.normalize(located))


def serve_located(monkeypatch, tmp_path, edit) -> None:
    """Make the located-incidents download return the fixture after `edit`."""
    lines = LOCATED.read_text(encoding="utf-8").splitlines()
    header = lines[0].split(",")
    path = tmp_path / "located.csv"
    path.write_text("\n".join(edit(header, lines)) + "\n", encoding="utf-8")
    monkeypatch.setattr(eodi, "LOCATED_URL", str(path))
    monkeypatch.setattr(eodi, "HEADERS", {})
    serve_unlocated(monkeypatch, UNLOCATED.read_bytes())


def test_download_keeps_the_country_code_na(monkeypatch, tmp_path):
    def edit(header, lines):
        row = lines[1].split(",")
        row[header.index("pais")] = "NA"
        return [lines[0], ",".join(row), *lines[2:]]

    serve_located(monkeypatch, tmp_path, edit)
    events = eodi.fetch()
    assert (
        events.loc[events["event_id"] == "eodi:EODI-2024-00003", "countries"].item()
        == "NA"
    )


def test_download_reads_an_empty_cell_as_missing(monkeypatch, tmp_path):
    def edit(header, lines):
        row = lines[1].split(",")
        row[header.index("pais")] = ""
        return [lines[0], ",".join(row), *lines[2:]]

    serve_located(monkeypatch, tmp_path, edit)
    events = eodi.fetch()
    assert pd.isna(
        events.loc[events["event_id"] == "eodi:EODI-2024-00003", "countries"].item()
    )
