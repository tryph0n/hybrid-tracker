"""The rules of tracker.schema.validate on the stored-format samples."""

import pandas as pd
import pytest

from tracker.schema import validate
from tracker.sources import read_all_events


@pytest.fixture
def events(sample_data_dir) -> pd.DataFrame:
    return read_all_events()


def test_samples_are_valid(events):
    validate(events)


def reorder_columns(events):
    return events[events.columns[::-1]]


def drop_all_rows(events):
    return events.iloc[0:0]


def set_blank(column, value):
    def change(events):
        events.loc[events.index[0], column] = value
        return events

    return change


def duplicate_id(events):
    events.loc[events.index[1], "event_id"] = events["event_id"].iloc[0]
    return events


def unknown_category(events):
    events.loc[events.index[0], "category"] = "balloon"
    return events


def bad_date(events):
    events.loc[events.index[0], "date"] = "2025-13-01"
    return events


def lat_without_lon(events):
    events.loc[events.index[0], "lon"] = None
    return events


def lat_out_of_range(events):
    events.loc[events.index[0], "lat"] = 91.0
    return events


def set_countries(value):
    def change(events):
        events.loc[events.index[0], "countries"] = value
        return events

    return change


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (reorder_columns, "Unexpected columns"),
        (drop_all_rows, "No events"),
        (set_blank("title", None), "without title"),
        (set_blank("title", ""), "without title"),
        (set_blank("title", "\t"), "without title"),
        (set_blank("url", "  "), "without url"),
        (set_blank("date", ""), "without date"),
        (duplicate_id, "Duplicate event_id"),
        (unknown_category, "Unknown category"),
        (bad_date, "doesn't match format"),
        (lat_without_lon, "both set or both empty"),
        (lat_out_of_range, "lat out of range"),
        (set_countries("Germany"), r"Invalid countries values: \['Germany'\]"),
        (set_countries("de"), r"Invalid countries values: \['de'\]"),
        (set_countries("DE,FR"), r"Invalid countries values: \['DE,FR'\]"),
        (set_countries("DE;"), r"Invalid countries values: \['DE;'\]"),
    ],
)
def test_validate_rejects(events, change, message):
    with pytest.raises(ValueError, match=message):
        validate(change(events.copy()))


def test_validate_accepts_several_country_codes(events):
    events.loc[events.index[0], "countries"] = "DE;PL"
    validate(events)
