"""Scope, passing rows, lines and counts of the dashboard, on frames built by hand."""

import pandas as pd
import pytest
from conftest import DATA_DIR

from tracker.schema import ATTRIBUTIONS, CATEGORIES, DOMAINS, read_csv
from tracker.sources import SOURCES
from tracker.views import (
    WhereSplit,
    coordinate_coverage,
    country_only,
    incident_lines,
    latest_dates,
    memberships,
    no_country_line,
    no_country_places,
    official_by_actor,
    passing_rows,
    per_source_values,
    published_sources,
    scope_rows,
    source_date,
    split_by_scope,
    split_line,
    where_split,
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
    table["geo_precision"] = table["lat"].map(
        lambda lat: "country" if pd.isna(lat) else "exact"
    )
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

    assert per_source_values(scope, "actors", NAMES)["e1"] == "— (EODI); RU;BY (Leiden)"


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


def lines_of(events):
    scope = scope_of(events)
    passing = passing_rows(scope, **ALL)
    return incident_lines(scope, passing, fields=[], names=NAMES), scope


def test_coordinate_coverage_splits_the_sources(events):
    _, scope = lines_of(events)

    coverage = coordinate_coverage(scope)

    assert coverage.sources_with == ["eodi"]
    assert coverage.sources_without == ["leiden"]


def test_the_split_adds_up_to_the_incidents(events):
    lines, _ = lines_of(events)

    split = where_split(lines)

    assert (split.total, split.located, split.countries_only, split.no_country) == (
        4,
        2,
        1,
        1,
    )
    assert not split.at_sea
    assert split_line(split) == (
        "4 incidents: 2 at a place (map), 1 in a country only (Countries view), "
        "1 with no country (listed below)"
    )


def test_the_split_line_of_the_fixture_lines_is_the_text_of_the_first_box():
    fixtures = [read_csv(path) for path in sorted(DATA_DIR.glob("*.csv"))]
    events = pd.concat(fixtures, ignore_index=True)
    events["date"] = pd.to_datetime(events["date"])
    events = with_code_lists(with_clusters(events))
    published = published_sources(events, list(SOURCES))
    scope = scope_rows(events, sources=published, order=published)
    passing = passing_rows(
        scope,
        domains=DOMAINS,
        categories=CATEGORIES,
        statuses=["confirmed", "unconfirmed"],
        attributions=ATTRIBUTIONS,
    )
    names = {name: source.short_name for name, source in SOURCES.items()}

    lines = incident_lines(scope, passing, fields=[], names=names)

    assert split_line(where_split(lines)) == (
        "17 incidents: 7 at a place (map), 9 in a country only (Countries view), "
        "1 at sea only (listed below)"
    )


def test_the_split_line_has_no_pointer_for_an_empty_part_and_a_singular():
    split = where_split_of(total=1, located=0, countries_only=1, no_country=0)

    assert split_line(split) == (
        "1 incident: 0 at a place, 1 in a country only (Countries view), 0 at sea only"
    )
    assert split_line(split, countries_pointer="Map page").endswith(
        "1 in a country only (Map page), 0 at sea only"
    )


def where_split_of(**counts):
    return WhereSplit(at_sea=True, **counts)


def test_the_line_takes_geo_precision_from_the_first_row_in_the_source_order(events):
    events.loc[events["event_id"] == "e1", "geo_precision"] = "place"
    events.loc[events["event_id"] == "l1", "geo_precision"] = "exact"
    lines, _ = lines_of(events)

    precision = lines.set_index("cluster_id")["geo_precision"]

    assert precision["e1"] == "place"


def test_an_empty_place_counts_under_no_place_in_the_source(events):
    events.loc[events["event_id"] == "l3", "place"] = ""
    lines, _ = lines_of(events)

    assert no_country_places(lines) == [("no place in the source", 1)]


def test_a_region_row_with_no_country_counts_as_with_no_country(events):
    events.loc[events["event_id"] == "l3", "geo_precision"] = "region"
    lines, _ = lines_of(events)

    split = where_split(lines)

    assert split.no_country == 1
    assert not split.at_sea
    assert split_line(split).endswith("1 with no country (listed below)")
    assert no_country_line(split, no_country_places(lines)).startswith(
        "With no country: "
    )


def test_the_places_of_the_incidents_at_sea_are_counted_in_a_fixed_order():
    places = ["Baltic Sea", "North Sea", "Adriatic Sea", "North Sea"]
    rows = [
        (f"s{n}", "leiden", "2025-01-01", "espionage", "reconnaissance", "confirmed")
        + ("none", "", "", f"s{n}")
        for n in range(len(places))
    ]
    table = frame(rows).assign(lat=None, lon=None, geo_precision="sea", place=places)
    lines, _ = lines_of(table)
    split = where_split(lines)

    found = no_country_places(lines)

    assert found == [
        ("North Sea", 2),
        ("Adriatic Sea", 1),
        ("Baltic Sea", 1),
    ]
    assert split.at_sea
    assert no_country_line(split, found) == (
        "At sea only, no country: North Sea (2), Adriatic Sea (1), Baltic Sea (1)."
    )


def test_a_table_without_cluster_columns_counts_each_row_once():
    table = pd.DataFrame({"event_id": ["a", "b"]})

    result = with_clusters(table)

    assert list(result["cluster_id"]) == ["a", "b"]
    assert result["is_canonical"].all()
    assert (result["n_sources"] == 1).all()


def test_a_source_date_keeps_the_precision_of_the_source():
    assert source_date("2026-11-20") == "2026-11-20"
    assert source_date("2026-01-13T14:22:43Z") == "2026-01-13 14:22 UTC"
    assert source_date("2026-11-26T03:15:00+02:00") == "2026-11-26 01:15 UTC"
    assert source_date(None) == ""
    assert source_date(float("nan")) == ""


def test_the_latest_dates_come_from_the_newest_run_of_each_source():
    quality = pd.DataFrame(
        {
            "source": ["a", "a", "b"],
            "run_at": ["2026-11-02T00:00:00+00:00"] + ["2026-11-01T00:00:00+00:00"] * 2,
            "downloaded_at": ["2026-11-02T01:02:00+00:00", "x", None],
            "updated_by_source": ["2026-11-02", "y", None],
        }
    )
    assert latest_dates(quality) == {
        "a": ("2026-11-02 01:02 UTC", "2026-11-02"),
        "b": ("", ""),
    }
    assert latest_dates(quality.drop(columns="downloaded_at")) == {}
    assert latest_dates(None) == {}


def test_split_by_scope_separates_the_list_and_applies_the_filter():
    in_country = pd.DataFrame({"value": ["DE", "US", "UA", "FR", "US"]})
    on_list, outside = split_by_scope(in_country)
    assert on_list["value"].tolist() == ["DE", "FR"]
    assert outside["value"].tolist() == ["US", "UA", "US"]
    on_list, outside = split_by_scope(in_country, ["US", "DE"])
    assert on_list["value"].tolist() == ["DE"]
    assert outside["value"].tolist() == ["US", "US"]


def fixture_rows_and_lines(**filters):
    fixtures = [read_csv(path) for path in sorted(DATA_DIR.glob("*.csv"))]
    events = pd.concat(fixtures, ignore_index=True)
    events["date"] = pd.to_datetime(events["date"])
    events = with_code_lists(with_clusters(events))
    events["domain_label"] = events["domain"].str.capitalize()
    published = published_sources(events, list(SOURCES))
    scope = scope_rows(events, sources=published, order=published)
    passing = passing_rows(
        scope,
        domains=DOMAINS,
        categories=CATEGORIES,
        statuses=["confirmed", "unconfirmed"],
        attributions=ATTRIBUTIONS,
        **filters,
    )
    names = {name: source.short_name for name, source in SOURCES.items()}
    return passing, incident_lines(scope, passing, fields=[], names=names)


def segments(bars):
    return {
        (row.code, row.domain): (row.solid, row.hatched)
        for row in bars.counts.itertuples()
    }


def test_country_only_counts_solid_and_hatched_parts_by_country_and_domain():
    rows, lines = fixture_rows_and_lines()

    bars = country_only(rows, lines)
    found = segments(bars)

    assert {code for code, _ in found}.isdisjoint({"US", "UA"})
    assert len({code for code, _ in found}) == 19
    assert found[("DE", "Information")] == (0, 3)
    assert found[("AT", "Information")] == (0, 1)
    assert found[("EE", "Information")] == (1, 0)
    assert found[("BG", "Sabotage")] == (1, 0)
    assert found[("BG", "Air")] == (0, 1)
    assert bars.counts[["solid", "hatched"]].to_numpy().sum() == 24
    assert bars.several == 4
    assert sorted(bars.outside["value"]) == ["UA", "US"]


def test_country_only_with_a_countries_filter_keeps_the_selected_states():
    rows, lines = fixture_rows_and_lines(countries=["DE"])
    germany = country_only(rows, lines, ["DE"])
    rows, lines = fixture_rows_and_lines(countries=["US"])
    states = country_only(rows, lines, ["US"])

    assert segments(germany) == {("DE", "Information"): (0, 3)}
    assert germany.several == 3
    assert states.counts.empty
    assert states.several == 0
    assert list(states.outside["value"]) == ["US"]


def test_the_split_line_has_no_pointer_when_nothing_shows_the_country_part():
    split = where_split_of(total=1, located=0, countries_only=1, no_country=0)

    assert split_line(split, countries_pointer=None) == (
        "1 incident: 0 at a place, 1 in a country only, 0 at sea only"
    )
