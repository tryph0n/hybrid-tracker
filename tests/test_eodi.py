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
                "russia_link": "none",
            },
        ),
        ("EODI-2025-00233", {"russia_link": "official", "countries": "PL"}),
        ("EODI-2026-00140", {"russia_link": "none", "category": "airspace_incursion"}),
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
                "russia_link": "none",
                "url": "https://droneobservatory.eu/EODI-2025-00140",
            },
        ),
        ("EODI-2025-00222", {"date": "2025-02-02", "countries": "MD"}),
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
    assert row[list(expected)].to_dict() == expected


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
    located.loc[0, "atribucion_actor"] = "Belarus"
    with pytest.raises(ValueError, match="ACTOR_IS_RUSSIA"):
        eodi.normalize(located)


def test_polish_name_of_russia_is_an_official_attribution():
    located = pd.read_csv(LOCATED)
    located.loc[0, "atribucion_actor"] = "Federacja Rosyjska"
    assert eodi.normalize(located).loc[0, "russia_link"] == "official"


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
