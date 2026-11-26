"""Scope, passing rows, lines and counts of the dashboard, on frames built by hand."""

import pandas as pd
import pytest

from tracker.views import (
    coordinate_coverage,
    incident_lines,
    memberships,
    official_by_actor,
    passing_rows,
    per_source_values,
    scope_rows,
    with_clusters,
    with_code_lists,
)

ORDER = ["eodi", "leiden"]
NAMES = {"eodi": "EODI", "leiden": "Leiden"}
COLUMNS = [
    "event_id",
    "source",
    "date",
    "domain",
    "category",
    "event_status",
    "attribution",
    "actors",
    "countries",
    "cluster_id",
    "title",
    "lat",
    "lon",
]

ALL = {
    "domains": ["air", "sabotage", "cyber", "espionage", "other"],
    "categories": [
        "drone_overflight",
        "airport_disruption",
        "arson",
        "intrusion",
        "reconnaissance",
        "unclassified",
    ],
    "statuses": ["confirmed", "unconfirmed", "dismissed"],
    "attributions": ["official", "assessed", "claimed", "none"],
}


def frame(rows):
    """Rows of (event_id, source, date, domain, category, status, attribution,
    actors, countries, cluster_id), with a title, a place and a date typed."""
    table = pd.DataFrame(
        [
            (*row, f"title of {row[0]}", 1.0 if row[1] == "eodi" else None, 2.0)
            for row in rows
        ],
        columns=COLUMNS,
    )
    table["date"] = pd.to_datetime(table["date"])
    table["lon"] = table["lat"]
    table["place"] = "place"
    table["end_date"] = pd.NaT
    table["date_precision"] = "day"
    return with_code_lists(table)


@pytest.fixture
def events():
    """Four incidents: three of one row, and `e1` that two sources record."""
    return frame(
        [
            (
                "e1",
                "eodi",
                "2025-10-04",
                "air",
                "drone_overflight",
                "dismissed",
                "none",
                "",
                "DE",
                "e1",
            ),
            (
                "l1",
                "leiden",
                "2025-10-01",
                "espionage",
                "reconnaissance",
                "confirmed",
                "official",
                "RU;BY",
                "DE;SE",
                "e1",
            ),
            (
                "e2",
                "eodi",
                "2025-11-01",
                "air",
                "airport_disruption",
                "confirmed",
                "official",
                "RU",
                "PL",
                "e2",
            ),
            (
                "l3",
                "leiden",
                "2025-12-01",
                "other",
                "unclassified",
                "confirmed",
                "claimed",
                "IR",
                "",
                "l3",
            ),
            (
                "l4",
                "leiden",
                "2025-12-05",
                "cyber",
                "intrusion",
                "unconfirmed",
                "assessed",
                "CN",
                "FR",
                "l4",
            ),
        ]
    )


def scope_of(events, sources=("eodi", "leiden")):
    return scope_rows(events, sources=sources, order=ORDER)


def listed(events, sources=("eodi", "leiden"), **filters):
    scope = scope_of(events, sources)
    return passing_rows(scope, **(ALL | filters))


def test_code_lists_of_empty_cells_are_empty(events):
    assert events["actor_codes"][0] == []
    assert events["country_codes"][3] == []
    assert events["actor_codes"][1] == ["RU", "BY"]


def test_the_source_filter_removes_the_rows_of_the_other_sources(events):
    scope = scope_of(events, ["leiden"])

    assert set(scope["source"]) == {"leiden"}
    assert list(scope["event_id"]) == ["l1", "l3", "l4"]


def test_the_line_row_is_the_first_row_in_the_source_order(events):
    reversed_rows = events.iloc[::-1]

    scope = scope_of(reversed_rows)

    line = scope[scope["is_line"]].set_index("cluster_id")
    assert line.loc["e1", "event_id"] == "e1"
    assert set(scope.loc[scope["cluster_id"] == "e1", "line_date"]) == {
        pd.Timestamp("2025-10-04")
    }


def test_the_line_row_moves_to_the_other_source_when_the_first_is_out_of_scope(events):
    scope = scope_of(events, ["leiden"])

    line = scope[scope["is_line"]].set_index("cluster_id")
    assert line.loc["e1", "event_id"] == "l1"
    assert set(scope.loc[scope["cluster_id"] == "e1", "line_date"]) == {
        pd.Timestamp("2025-10-01")
    }


def test_empty_actor_and_country_selection_keeps_every_row(events):
    assert len(listed(events)) == len(events)


def test_actor_selection_keeps_the_rows_that_name_the_state(events):
    assert list(listed(events, actors=["BY"])["event_id"]) == ["l1"]


def test_attribution_is_a_filter_not_an_inclusion_test(events):
    kept = listed(events, attributions=["none"])
    assert list(kept["event_id"]) == ["e1"]


def test_country_selection(events):
    assert list(listed(events, countries=["SE"])["event_id"]) == ["l1"]


def test_a_filter_tests_its_conditions_on_the_same_row(events):
    kept = listed(events, domains=["air"], statuses=["confirmed", "unconfirmed"])

    assert list(kept["event_id"]) == ["e2"]


def test_an_incident_stays_when_one_source_dismisses_it_and_another_does_not(events):
    kept = listed(events, statuses=["confirmed", "unconfirmed"])

    assert set(kept["cluster_id"]) == {"e1", "e2", "l3", "l4"}


def test_values_that_agree_show_once():
    rows = frame(
        [
            ("a", "eodi", "2025-10-04", "air", "x", "s", "none", "", "DE", "a"),
            ("b", "leiden", "2025-10-04", "air", "x", "s", "none", "", "DE", "a"),
        ]
    )

    values = per_source_values(scope_of(rows), "domain", NAMES)

    assert values["a"] == "air"


def test_values_that_differ_show_with_the_name_of_each_source(events):
    scope = scope_of(events)

    assert per_source_values(scope, "domain", NAMES)["e1"] == (
        "air (EODI); espionage (Leiden)"
    )


def test_an_empty_value_stays_empty_for_its_source(events):
    scope = scope_of(events)

    assert per_source_values(scope, "actors", NAMES)["e1"] == "(EODI); RU;BY (Leiden)"


def test_the_line_takes_title_and_date_from_the_first_row_and_values_from_all(events):
    scope = scope_of(events)
    passing = passing_rows(scope, **ALL)

    lines = incident_lines(
        scope, passing, fields=["domain", "event_status"], names=NAMES
    ).set_index("cluster_id")

    line = lines.loc["e1"]
    assert line["title"] == "title of e1"
    assert line["date"] == pd.Timestamp("2025-10-04")
    assert line["line_source"] == "eodi"
    assert line["domain"] == "air (EODI); espionage (Leiden)"
    assert line["event_status"] == "dismissed (EODI); confirmed (Leiden)"
    assert list(lines.index) == ["e1", "e2", "l3", "l4"]


def test_a_line_does_not_change_with_a_filter_other_than_the_source(events):
    scope = scope_of(events)
    fields = ["domain", "event_status", "countries"]

    def line(**filters):
        passing = passing_rows(scope, **(ALL | filters))
        lines = incident_lines(scope, passing, fields=fields, names=NAMES)
        return lines.set_index("cluster_id").loc["e1"]

    reference = line()
    for filters in (
        {"domains": ["espionage"]},
        {"statuses": ["confirmed"]},
        {"statuses": ["dismissed"]},
        {"countries": ["SE"]},
        {"actors": ["BY"]},
        {"attributions": ["official"]},
    ):
        assert line(**filters).equals(reference), filters


def test_the_line_of_a_filtered_out_incident_is_not_listed(events):
    scope = scope_of(events)
    passing = passing_rows(scope, **(ALL | {"domains": ["cyber"]}))

    lines = incident_lines(scope, passing, fields=["domain"], names=NAMES)

    assert list(lines["cluster_id"]) == ["l4"]


def test_has_country_follows_the_passing_rows(events):
    scope = scope_of(events)
    passing = passing_rows(scope, **ALL)

    lines = incident_lines(scope, passing, fields=[], names=NAMES)

    assert dict(zip(lines["cluster_id"], lines["has_country"], strict=True)) == {
        "e1": True,
        "e2": True,
        "l3": False,
        "l4": True,
    }


def test_an_incident_counts_in_each_domain_of_its_passing_rows(events):
    both = memberships(listed(events), "domain")
    espionage_only = memberships(listed(events, domains=["espionage"]), "domain")

    assert sorted(zip(both["cluster_id"], both["value"], strict=True)) == [
        ("e1", "air"),
        ("e1", "espionage"),
        ("e2", "air"),
        ("l3", "other"),
        ("l4", "cyber"),
    ]
    assert list(
        zip(espionage_only["cluster_id"], espionage_only["value"], strict=True)
    ) == [("e1", "espionage")]


def test_the_counts_by_domain_add_up_to_more_than_the_incidents(events):
    rows = listed(events)

    assert rows["cluster_id"].nunique() == 4
    assert len(memberships(rows, "domain")) == 5


def test_an_incident_counts_once_in_a_country_that_two_sources_give(events):
    shared = frame(
        [
            (
                "a",
                "eodi",
                "2025-10-04",
                "air",
                "drone_overflight",
                "confirmed",
                "none",
                "",
                "DE",
                "a",
            ),
            (
                "b",
                "leiden",
                "2025-10-04",
                "air",
                "drone_overflight",
                "confirmed",
                "none",
                "",
                "DE;SE",
                "a",
            ),
        ]
    )

    found = memberships(listed(shared), "country_codes")

    assert sorted(found["value"]) == ["DE", "SE"]


def test_a_membership_carries_the_date_of_the_line(events):
    found = memberships(listed(events), "domain")

    dates = found.set_index(["cluster_id", "value"])["line_date"]
    assert dates[("e1", "espionage")] == pd.Timestamp("2025-10-04")


def test_the_official_count_by_state_uses_the_official_rows_only(events):
    rows = listed(events)

    assert official_by_actor(rows) == {"RU": 2, "BY": 1}
    assert official_by_actor(rows[rows["attribution"] == "none"]) == {}


def test_coordinate_coverage_splits_lines_and_sources(events):
    scope = scope_of(events)
    passing = passing_rows(scope, **ALL)
    lines = incident_lines(scope, passing, fields=[], names=NAMES)

    coverage = coordinate_coverage(lines, scope)

    assert coverage.sources_with == ["eodi"]
    assert coverage.sources_without == ["leiden"]
    assert (coverage.located, coverage.countries_only, coverage.on_no_map) == (2, 1, 1)


def test_a_table_without_cluster_columns_counts_each_row_once():
    table = pd.DataFrame({"event_id": ["a", "b"]})

    result = with_clusters(table)

    assert list(result["cluster_id"]) == ["a", "b"]
    assert result["is_canonical"].all()
    assert (result["n_sources"] == 1).all()
