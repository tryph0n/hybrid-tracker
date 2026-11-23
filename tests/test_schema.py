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


def blank_title(events):
    events.loc[events.index[0], "title"] = None
    return events


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


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (reorder_columns, "Unexpected columns"),
        (drop_all_rows, "No events"),
        (blank_title, "without title"),
        (duplicate_id, "Duplicate event_id"),
        (unknown_category, "Unknown category"),
        (bad_date, "doesn't match format"),
        (lat_without_lon, "both set or both empty"),
        (lat_out_of_range, "lat out of range"),
    ],
)
def test_validate_rejects(events, change, message):
    with pytest.raises(ValueError, match=message):
        validate(change(events.copy()))
