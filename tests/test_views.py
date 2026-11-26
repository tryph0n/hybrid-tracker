"""Filters and headline counts of the dashboard, on frames built by hand."""

import pandas as pd
import pytest

from tracker.views import (
    coordinate_coverage,
    filter_events,
    official_by_actor,
    with_clusters,
    with_code_lists,
)

ROWS = [
    ("eodi", "air", "drone_overflight", "confirmed", "none", "", "DE"),
    ("eodi", "air", "airport_disruption", "confirmed", "official", "RU", "PL"),
    ("leiden", "sabotage", "arson", "unconfirmed", "assessed", "RU", "FR"),
    ("leiden", "cyber", "intrusion", "confirmed", "official", "RU;BY", "LT"),
    ("leiden", "cyber", "intrusion", "confirmed", "official", "CN", "LT"),
    ("leiden", "other", "unclassified", "dismissed", "claimed", "IR", ""),
]


@pytest.fixture
def events():
    frame = pd.DataFrame(
        ROWS,
        columns=[
            "source",
            "domain",
            "category",
            "event_status",
            "attribution",
            "actors",
            "countries",
        ],
    )
    return with_code_lists(
        with_clusters(frame.assign(event_id=frame.index.astype(str)))
    )


ALL = {
    "sources": ["eodi", "leiden"],
    "domains": ["air", "sabotage", "cyber", "other"],
    "categories": [
        "drone_overflight",
        "airport_disruption",
        "arson",
        "intrusion",
        "unclassified",
    ],
    "statuses": ["confirmed", "unconfirmed", "dismissed"],
    "attributions": ["official", "assessed", "claimed", "none"],
}


def test_code_lists_of_empty_cells_are_empty(events):
    assert events["actor_codes"][0] == []
    assert events["country_codes"][5] == []
    assert events["actor_codes"][3] == ["RU", "BY"]


def test_empty_actor_and_country_selection_keeps_every_event(events):
    assert len(filter_events(events, **ALL)) == len(events)


def test_actor_selection_keeps_the_events_that_name_the_state(events):
    kept = filter_events(events, **ALL, actors=["BY"])
    assert list(kept.index) == [3]


def test_attribution_is_a_filter_not_an_inclusion_test(events):
    kept = filter_events(events, **(ALL | {"attributions": ["none"]}))
    assert list(kept.index) == [0]


def test_country_selection(events):
    kept = filter_events(events, **ALL, countries=["LT"])
    assert list(kept.index) == [3, 4]


def test_official_counts_only_official_events_by_actor(events):
    assert official_by_actor(events) == {"RU": 2, "BY": 1, "CN": 1}
    only_none = events[events["attribution"] == "none"]
    assert official_by_actor(only_none) == {}


def test_coordinate_coverage_splits_events_and_sources():
    frame = pd.DataFrame(
        [
            ("eodi", 1.0, 2.0, "DE"),
            ("eodi", None, None, "PL"),
            ("leiden", None, None, "FR"),
            ("leiden", None, None, None),
            ("leiden", None, None, None),
        ],
        columns=["source", "lat", "lon", "countries"],
    )
    coverage = coordinate_coverage(frame)
    assert coverage.sources_with == ["eodi"]
    assert coverage.sources_without == ["leiden"]
    assert (coverage.located, coverage.countries_only, coverage.on_no_map) == (1, 2, 2)


def test_an_event_with_coordinates_and_a_country_counts_once():
    frame = pd.DataFrame(
        [("eodi", 1.0, 2.0, "DE")], columns=["source", "lat", "lon", "countries"]
    )
    coverage = coordinate_coverage(frame)
    assert (coverage.located, coverage.countries_only, coverage.on_no_map) == (1, 0, 0)
    assert coverage.sources_without == []


CLUSTER_ALL = ALL | {
    "domains": ["air", "espionage", "other"],
    "categories": [
        "drone_overflight",
        "airport_disruption",
        "reconnaissance",
        "unclassified",
    ],
}


@pytest.fixture
def clustered():
    rows = [
        (
            "e1",
            "eodi",
            "air",
            "drone_overflight",
            "dismissed",
            "none",
            "",
            "DE",
            "e1",
            True,
        ),
        (
            "l1",
            "leiden",
            "espionage",
            "reconnaissance",
            "confirmed",
            "assessed",
            "RU",
            "DE",
            "e1",
            False,
        ),
        (
            "e2",
            "eodi",
            "air",
            "airport_disruption",
            "confirmed",
            "none",
            "",
            "PL",
            "e2",
            True,
        ),
        (
            "l3",
            "leiden",
            "other",
            "unclassified",
            "confirmed",
            "claimed",
            "IR",
            "FR",
            "l3",
            True,
        ),
    ]
    frame = pd.DataFrame(
        rows,
        columns=[
            "event_id",
            "source",
            "domain",
            "category",
            "event_status",
            "attribution",
            "actors",
            "countries",
            "cluster_id",
            "is_canonical",
        ],
    ).assign(n_sources=[2, 2, 1, 1])
    return with_code_lists(frame)


def test_a_cluster_is_returned_as_its_canonical_record(clustered):
    kept = filter_events(clustered, **CLUSTER_ALL)

    assert list(kept["event_id"]) == ["e1", "e2", "l3"]


def test_a_cluster_stays_when_one_source_dismisses_it_and_another_does_not(clustered):
    confirmed = filter_events(clustered, **(CLUSTER_ALL | {"statuses": ["confirmed"]}))
    dismissed = filter_events(clustered, **(CLUSTER_ALL | {"statuses": ["dismissed"]}))

    assert list(confirmed["event_id"]) == ["l1", "e2", "l3"]
    assert list(dismissed["event_id"]) == ["e1"]


def test_a_cluster_counts_once_in_each_source_of_its_rows(clustered):
    leiden = filter_events(clustered, **(CLUSTER_ALL | {"sources": ["leiden"]}))
    eodi = filter_events(clustered, **(CLUSTER_ALL | {"sources": ["eodi"]}))

    assert list(leiden["event_id"]) == ["l1", "l3"]
    assert list(eodi["event_id"]) == ["e1", "e2"]


def test_a_cluster_counts_once_in_the_domain_of_the_row_it_shows(clustered):
    air = filter_events(clustered, **(CLUSTER_ALL | {"domains": ["air"]}))
    espionage = filter_events(clustered, **(CLUSTER_ALL | {"domains": ["espionage"]}))
    both = filter_events(clustered, **(CLUSTER_ALL | {"domains": ["air", "espionage"]}))

    assert list(air["event_id"]) == ["e1", "e2"]
    assert list(espionage["event_id"]) == ["l1"]
    assert list(both["event_id"]) == ["e1", "e2"]


def test_the_row_filters_apply_to_one_row_of_the_cluster(clustered):
    both = CLUSTER_ALL | {"sources": ["leiden"], "statuses": ["dismissed"]}

    assert filter_events(clustered, **both).empty


def test_a_table_without_cluster_columns_counts_each_row_once():
    frame = pd.DataFrame({"event_id": ["a", "b"]})

    result = with_clusters(frame)

    assert list(result["cluster_id"]) == ["a", "b"]
    assert result["is_canonical"].all()
    assert (result["n_sources"] == 1).all()
