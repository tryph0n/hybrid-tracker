"""The EODI adapter on recorded samples of the two upstream files."""

import io
import json
from typing import ClassVar

import pandas as pd
import pytest
from conftest import FIXTURES

from tracker import eodi
from tracker.schema import validate

LOCATED = FIXTURES / "eodi_incidentes.csv"
UNLOCATED = FIXTURES / "eodi_sin_ubicacion.json"


@pytest.fixture
def events() -> pd.DataFrame:
    located = pd.read_csv(LOCATED)
    return eodi.map_frame(
        pd.concat(
            [located, eodi.reshape_unlocated(UNLOCATED.read_bytes())],
            ignore_index=True,
        )
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
        eodi.map_frame(located)


@pytest.mark.parametrize(
    ("name", "code"),
    [("Federacja Rosyjska", "RU"), ("Belarus", "BY"), ("Ucraina", "UA")],
)
def test_actor_name_maps_to_an_iso_code(name, code):
    located = pd.read_csv(LOCATED)
    located.loc[0, "atribucion_actor"] = name
    located.loc[0, "atribucion_autoridad"] = "Some office"
    row = eodi.map_frame(located).loc[0]
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
        eodi.map_frame(located)


def test_unknown_date_precision_stops_the_ingestion():
    located = pd.read_csv(LOCATED)
    located.loc[0, "inicio_precision"] = "semana"
    with pytest.raises(ValueError, match="DATE_PRECISION"):
        eodi.map_frame(located)


def test_attributed_unlocated_incident_stops_the_ingestion():
    payload = json.loads(UNLOCATED.read_text(encoding="utf-8"))
    payload["incidentes"][1]["estado"]["actual"] = "atribuido"
    with pytest.raises(ValueError, match="Attributed unlocated incident"):
        eodi.reshape_unlocated(json.dumps(payload).encode())


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
        validate(eodi.map_frame(located))


def edited_payloads(edit) -> dict[str, bytes]:
    """The two fixture files, with the located CSV lines changed by `edit`."""
    lines = LOCATED.read_text(encoding="utf-8").splitlines()
    header = lines[0].split(",")
    return {
        eodi.LOCATED_NAME: ("\n".join(edit(header, lines)) + "\n").encode(),
        eodi.UNLOCATED_NAME: UNLOCATED.read_bytes(),
    }


def test_payloads_keep_the_country_code_na():
    def edit(header, lines):
        row = lines[1].split(",")
        row[header.index("pais")] = "NA"
        return [lines[0], ",".join(row), *lines[2:]]

    events = eodi.normalize(edited_payloads(edit))
    assert (
        events.loc[events["event_id"] == "eodi:EODI-2024-00003", "countries"].item()
        == "NA"
    )


def test_payloads_read_an_empty_cell_as_missing():
    def edit(header, lines):
        row = lines[1].split(",")
        row[header.index("pais")] = ""
        return [lines[0], ",".join(row), *lines[2:]]

    events = eodi.normalize(edited_payloads(edit))
    assert pd.isna(
        events.loc[events["event_id"] == "eodi:EODI-2024-00003", "countries"].item()
    )


def test_normalize_of_the_payloads_equals_the_mapped_frames(events):
    files = {
        eodi.LOCATED_NAME: LOCATED.read_bytes(),
        eodi.UNLOCATED_NAME: UNLOCATED.read_bytes(),
    }
    pd.testing.assert_frame_equal(
        eodi.normalize(files).sort_values("event_id", ignore_index=True),
        events.sort_values("event_id", ignore_index=True),
        check_dtype=False,
    )


class Answer(io.BytesIO):
    status = 200


def test_extract_keeps_both_downloads_as_served(monkeypatch):
    served = {
        eodi.LOCATED_URL: LOCATED.read_bytes(),
        eodi.UNLOCATED_URL: UNLOCATED.read_bytes(),
    }
    monkeypatch.setattr(
        eodi.urllib.request,
        "urlopen",
        lambda request, timeout: Answer(served[request.full_url]),
    )
    payloads = eodi.extract()
    assert [(p.source, p.name, p.url, p.status) for p in payloads] == [
        ("eodi", eodi.LOCATED_NAME, eodi.LOCATED_URL, 200),
        ("eodi", eodi.UNLOCATED_NAME, eodi.UNLOCATED_URL, 200),
    ]
    assert [p.content for p in payloads] == list(served.values())


class AnswerWithHeaders(Answer):
    headers: ClassVar[dict[str, str]] = {
        "Last-Modified": "Fri, 27 Nov 2026 08:54:51 GMT"
    }


def unlocated_with_dates(*dates: str | None) -> bytes:
    document = json.loads(UNLOCATED.read_bytes())
    for incident, date in zip(document["incidentes"], dates, strict=False):
        if date:
            incident["control"] = {
                "ultima_actualizacion": {"valor": date, "precision": "minuto"}
            }
    return json.dumps(document).encode()


def test_the_date_of_the_last_change_is_the_newest_row_date_not_the_header(
    monkeypatch,
):
    served = {
        eodi.LOCATED_URL: LOCATED.read_bytes(),
        eodi.UNLOCATED_URL: unlocated_with_dates(
            "2026-11-12T14:00Z", "2026-11-14T09:05Z"
        ),
    }
    monkeypatch.setattr(
        eodi.urllib.request,
        "urlopen",
        lambda request, timeout: AnswerWithHeaders(served[request.full_url]),
    )
    files = {p.name: p.content for p in eodi.extract()}
    assert eodi.updated_by_source(files) == "2026-11-14T09:05:00Z"


def test_the_newest_date_can_come_from_the_csv_file():
    files = {
        eodi.LOCATED_NAME: LOCATED.read_bytes(),
        eodi.UNLOCATED_NAME: unlocated_with_dates("2026-11-01T00:00Z"),
    }
    assert eodi.updated_by_source(files) == "2026-11-12T14:17:00Z"


def test_a_file_set_without_a_row_date_gives_no_date():
    header_only = LOCATED.read_bytes().split(b"\n")[0] + b"\n"
    files = {
        eodi.LOCATED_NAME: header_only,
        eodi.UNLOCATED_NAME: UNLOCATED.read_bytes(),
    }
    assert eodi.updated_by_source(files) is None
