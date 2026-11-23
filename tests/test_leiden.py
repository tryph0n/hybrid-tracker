"""The Leiden adapter on a hand-written sample of the EventData sheet."""

import pandas as pd
import pytest
from conftest import FIXTURES

from tracker import leiden
from tracker.schema import validate

SAMPLE = FIXTURES / "leiden_eventdata.csv"


def event_data(tmp_path, raw: pd.DataFrame) -> pd.DataFrame:
    """Round-trip `raw` through an xlsx file, as the real download is read."""
    book = tmp_path / "RussianOps.xlsx"
    raw.to_excel(book, sheet_name="EventData", index=False)
    return pd.read_excel(book, sheet_name="EventData")


@pytest.fixture
def events(tmp_path) -> pd.DataFrame:
    return leiden.normalize(event_data(tmp_path, pd.read_csv(SAMPLE)))


def test_normalize_follows_schema(events):
    validate(events)
    # The three note rows below the events are dropped.
    assert len(events) == 7
    assert (events["date_precision"] == "month").all()
    assert (events["event_status"] == "confirmed").all()
    assert (events["russia_link"] == "assessed").all()
    assert events[["lat", "lon"]].isna().all().all()
    assert (events["url"] == leiden.DATASET_URL).all()


@pytest.mark.parametrize(
    ("event_id", "expected"),
    [
        (
            "leiden:002",
            {"date": "2022-01-01", "countries": "", "category": "reconnaissance"},
        ),
        (
            "leiden:003",
            {
                "date": "2022-03-01",
                "countries": "AT;DE",
                "place": "Austria; Germany",
                "category": "influence",
                "subcategory": "Influence operation",
            },
        ),
        ("leiden:004", {"countries": "EE", "category": "influence"}),
        (
            "leiden:005",
            {"date": "2023-01-01", "countries": "NL", "category": "sabotage_attack"},
        ),
        ("leiden:006", {"countries": "DE", "category": "sabotage_attack"}),
        ("leiden:007", {"countries": "CZ;GB;DE", "category": "other"}),
        ("leiden:008", {"date": "2025-12-01", "countries": "PL;DE"}),
    ],
)
def test_normalize_maps_values(events, event_id, expected):
    row = events.set_index("event_id").loc[event_id]
    assert row[list(expected)].to_dict() == expected


def test_title_is_stripped(events):
    assert events.set_index("event_id").loc["leiden:006", "title"].endswith("sample)")


def test_unknown_country_stops_the_ingestion(tmp_path):
    raw = pd.read_csv(SAMPLE)
    raw.loc[2, "Where"] = "Ruritania"
    with pytest.raises(LookupError):
        leiden.normalize(event_data(tmp_path, raw))


def test_unknown_category_fails_validation(tmp_path):
    raw = pd.read_csv(SAMPLE)
    raw.loc[2, "EventCategory"] = "Cyber attack"
    with pytest.raises(ValueError, match="without category"):
        validate(leiden.normalize(event_data(tmp_path, raw)))
