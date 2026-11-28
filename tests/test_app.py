"""The dashboard run headless with AppTest, on samples."""

import base64
import dataclasses
import importlib
import io
import json
import logging
import re
import zipfile
from datetime import date

import numpy as np
import pandas as pd
import plotly.io as pio
import pyarrow as pa
import pytest
import streamlit as st
from conftest import (
    APP,
    FIXTURES,
    REVISION,
    REVISION_DATE,
    events_path,
    geonames_archive,
    read_all_events,
    read_events,
    recorded_payloads,
)
from streamlit.elements.lib import policies
from streamlit.runtime.caching import cache_utils
from streamlit.testing.v1 import AppTest

from tracker import actors, build, clock, geonames, hub, ingest, quality, stanford
from tracker.clock import utc_today
from tracker.schema import (
    ATTRIBUTIONS,
    CATEGORY_DOMAINS,
    DOMAINS,
    EUROPEAN_STATES,
    PUBLISHED_COLUMNS,
    read_csv,
)
from tracker.sources import SOURCES
from tracker.store import LocalBackend, Payload, Store


@pytest.fixture(autouse=True)
def clear_streamlit_cache():
    """The app caches the loaded events: every run must read the files again."""
    st.cache_data.clear()


@pytest.fixture(autouse=True)
def fake_hub(hub_fetches):
    """The app reads the Hub through `hub_fetches`, never the network."""


def run_app() -> AppTest:
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.run()
    assert not at.exception, [error.value for error in at.exception]
    return at


def metrics(at: AppTest) -> dict[str, str]:
    return {metric.label: metric.value for metric in at.metric}


def expected_events(events):
    """Rows shown with the default filters: every status except dismissed."""
    return events[events["event_status"] != "dismissed"]


def chart(at: AppTest, index: int):
    return pio.from_json(at.get("plotly_chart")[index].proto.spec)


def captions(at: AppTest) -> str:
    return " ".join(caption.value for caption in at.caption)


def counts(bar) -> list[int]:
    """Bar lengths: Plotly 7 sends numeric arrays as base64."""
    return np.frombuffer(
        base64.b64decode(bar.x["bdata"]), dtype=bar.x["dtype"]
    ).tolist()


def open_sea_rows(copies: int):
    """Write `copies` events with no country and no coordinates as the Leiden file."""
    events = read_all_events()
    template = events[events["lat"].isna() & events["countries"].isna()].iloc[0]
    rows = [template.to_dict() | {"event_id": f"leiden:S-{n}"} for n in range(copies)]
    pd.DataFrame(rows).to_csv(events_path("leiden"), index=False)


def dismiss_vilnius():
    """Set the sample row `EODI-2025-00112` to dismissed: no sample row is."""
    eodi = read_events("eodi")
    eodi.loc[eodi["event_id"] == "eodi:EODI-2025-00112", "event_status"] = "dismissed"
    eodi.to_csv(events_path("eodi"), index=False)


def share_places(count: int):
    """Move `count` shown, located EODI events and the dismissed ones to one place."""
    dismiss_vilnius()
    eodi = read_events("eodi")
    located = eodi[eodi["lat"].notna()]
    shown = expected_events(located)
    dismissed = located[located["event_status"] == "dismissed"]
    place = shown.iloc[0][["lat", "lon"]].tolist()
    eodi.loc[shown.index[:count].append(dismissed.index), ["lat", "lon"]] = place
    eodi.to_csv(events_path("eodi"), index=False)


def test_counts_on_the_samples(sample_data_dir):
    at = run_app()
    shown = expected_events(read_all_events())
    codes = {code for cell in shown["countries"].dropna() for code in cell.split(";")}
    kpis = metrics(at)
    assert kpis["Incidents"] == str(len(shown))
    assert len(codes) == 22
    assert kpis["Countries"] == str(len(codes & EUROPEAN_STATES)) == "20"
    assert kpis["Officially attributed"] == str(
        (shown["attribution"] == "official").sum()
    )
    assert len(at.get("plotly_chart")) == 4


def test_sources_filter_lists_every_source_all_selected(sample_data_dir):
    at = run_app()
    sources = at.multiselect(key="sources")
    assert sources.options == [SOURCES[name].title for name in SOURCES]
    assert sources.value == list(SOURCES)


def test_every_domain_is_a_filter_option(sample_data_dir):
    at = run_app()
    domains = at.multiselect(key="domains")
    assert domains.label == "Domain"
    assert len(domains.options) == len(DOMAINS)
    assert domains.value == DOMAINS


def test_category_options_follow_the_selected_domains(sample_data_dir):
    at = run_app()
    categories = at.multiselect(key="categories")
    assert len(categories.options) == len(CATEGORY_DOMAINS)
    at.multiselect(key="domains").set_value(["air", "violence"]).run()
    assert not at.exception
    categories = at.multiselect(key="categories")
    expected = [c for c, d in CATEGORY_DOMAINS.items() if d in ("air", "violence")]
    assert len(categories.options) == len(expected)
    assert len(categories.value) == len(expected)


def test_attribution_filter_starts_with_the_four_levels(sample_data_dir):
    at = run_app()
    attributions = at.multiselect(key="attributions")
    assert attributions.value == ATTRIBUTIONS
    assert len(attributions.options) == 4


def test_attribution_filter_never_hides_events_by_default(sample_data_dir):
    at = run_app()
    shown = expected_events(read_all_events())
    assert (shown["attribution"] == "none").any()
    assert metrics(at)["Incidents"] == str(len(shown))
    at.multiselect(key="attributions").set_value(["official"]).run()
    assert not at.exception
    assert metrics(at)["Incidents"] == str((shown["attribution"] == "official").sum())


def test_official_counts_only_official_events_of_the_selection(sample_data_dir):
    at = run_app()
    shown = expected_events(read_all_events())
    official = shown[shown["attribution"] == "official"]
    assert 0 < len(official) < len(shown)
    assert metrics(at)["Officially attributed"] == str(len(official))
    at.multiselect(key="sources").set_value(["leiden"]).run()
    assert metrics(at)["Officially attributed"] == "0"
    assert metrics(at)["Incidents"] == str((shown["source"] == "leiden").sum())
    help_text = next(m.help for m in at.metric if m.label == "Officially attributed")
    assert "No event of this selection" in help_text


def test_official_help_gives_the_split_by_actor(sample_data_dir):
    at = run_app()
    help_text = next(m.help for m in at.metric if m.label == "Officially attributed")
    assert "Russian Federation 1" in help_text


def test_empty_actor_selection_keeps_events_with_no_state(sample_data_dir):
    at = run_app()
    shown = expected_events(read_all_events())
    assert at.multiselect(key="actors").value == []
    assert metrics(at)["Incidents"] == str(len(shown))
    at.multiselect(key="actors").set_value(["RU"]).run()
    assert not at.exception
    assert metrics(at)["Incidents"] == str(
        shown["actors"].fillna("").str.contains("RU").sum()
    )


def test_every_chart_is_coloured_by_domain(sample_data_dir):
    at = run_app()
    names = {trace.name for trace in chart(at, 0).data}
    assert names <= {"Air", "Information", "Sabotage", "Violence", "Espionage"}
    assert {trace.name for trace in chart(at, 1).data} == {"Air"}


def test_table_and_map_state_who_attributed(sample_data_dir):
    at = run_app()
    table = at.dataframe[0].value
    for column in ["attribution_label", "attributed_by_label", "end_date"]:
        assert column in table.columns
    assert "Polish government" in set(table["attributed_by_label"])
    hover = chart(at, 1).data[0].hovertemplate
    assert "Attribution=" in hover
    assert "Attributed by=" in hover


NONE_LABEL = "No attribution in the source"


def test_map_hover_of_an_unattributed_event_shows_the_data(sample_data_dir):
    at = run_app()
    traces = chart(at, 1).data

    def hover_values(label):
        pattern = rf"{label}=%\{{customdata\[(\d+)\]"
        index = int(re.search(pattern, traces[0].hovertemplate)[1])
        return [row[index] for trace in traces for row in trace.customdata]

    levels = hover_values("Attribution")
    holders = hover_values("Attributed by")
    actors = hover_values("Actors")
    assert NONE_LABEL in levels
    assert "" in actors
    for label in (
        "Date",
        "Place",
        "Category",
        "Attribution",
        "Attributed by",
        "Actors",
    ):
        for value in hover_values(label):
            assert str(value).lower() not in {"nan", "none"}, label
    for level, holder in zip(levels, holders, strict=True):
        if level == NONE_LABEL:
            assert holder == ""


def test_credits_every_source(sample_data_dir):
    at = run_app()
    for source in SOURCES.values():
        assert source.citation in captions(at)
    table = at.dataframe[-1].value
    assert list(table["Source"]) == [source.title for source in SOURCES.values()]
    assert list(table["Licence"]) == [source.licence for source in SOURCES.values()]


def test_empty_selection_stops_with_a_message(sample_data_dir):
    at = run_app()
    at.multiselect(key="sources").set_value([]).run()
    assert not at.exception
    assert at.info[0].value == "No event matches these filters."
    assert not at.metric


def test_source_without_coordinates_keeps_the_other_charts(sample_data_dir):
    at = run_app()
    at.multiselect(key="sources").set_value(["leiden"]).run()
    assert not at.exception
    assert "Countries view" not in at.info[0].value
    assert len(at.get("plotly_chart")) == 3


def test_countries_view(sample_data_dir):
    at = run_app()
    at.segmented_control[0].set_value("Countries").run()
    assert not at.exception
    assert not at.info
    assert len(at.get("plotly_chart")) == 3


def test_country_filter_restricts_kpi_and_bar(sample_data_dir):
    at = run_app()
    at.multiselect(key="countries").set_value(["DE"]).run()
    assert not at.exception
    assert metrics(at)["Countries"] == "1"
    assert chart(at, 3).data[0].y == ("Germany",)
    at.segmented_control[0].set_value("Countries").run()
    assert chart(at, 1).data[0].locations == ("DEU",)


def countries_help(at: AppTest) -> str:
    return next(m.help for m in at.metric if m.label == "Countries")


def test_counts_by_country_keep_the_european_list_only(sample_data_dir):
    at = run_app()
    help_text = countries_help(at)
    assert help_text.startswith(
        "States of the European list of the data scope (43 states) that the "
        "selected incidents name."
    )
    assert help_text.endswith("not counted here: Ukraine (1), United States (1).")
    assert "decision record" not in help_text
    assert set(chart(at, 3).data[0].y).isdisjoint({"United States", "Ukraine"})
    assert len(chart(at, 3).data[0].y) == 20
    at.segmented_control[0].set_value("Countries").run()
    assert set(chart(at, 1).data[0].locations).isdisjoint({"USA", "UKR"})
    assert "states of the European list only" in captions(at)


def test_country_filter_on_a_state_outside_the_list_counts_nothing(sample_data_dir):
    at = run_app()
    at.multiselect(key="countries").set_value(["US"]).run()
    assert not at.exception
    assert metrics(at)["Countries"] == "0"
    help_text = countries_help(at)
    assert "United States (1)" in help_text
    assert "Ukraine" not in help_text
    assert all(len(trace.y) == 0 for trace in chart(at, -1).data)
    at.segmented_control[0].set_value("Countries").run()
    assert all(len(trace.locations) == 0 for trace in chart(at, 1).data)


def test_country_filter_on_germany_has_no_outside_sentence(sample_data_dir):
    at = run_app()
    at.multiselect(key="countries").set_value(["DE"]).run()
    assert metrics(at)["Countries"] == "1"
    assert "not counted here" not in countries_help(at)


def test_the_events_table_and_the_filter_keep_the_states_outside_the_list(
    sample_data_dir,
):
    at = run_app()
    assert "United States" in at.multiselect(key="countries").options
    table = event_table(at)
    row = table[table["country_names"].str.contains("United States")]
    assert len(row) == 1
    assert "Ukraine" in row.iloc[0]["country_names"]


def test_multi_country_events_count_for_each_selected_country(sample_data_dir):
    at = run_app()
    at.multiselect(key="countries").set_value(["AT", "DE"]).run()
    assert not at.exception
    assert metrics(at)["Countries"] == "2"
    bars = chart(at, 3).data[0]
    assert dict(zip(bars.y, counts(bars))) == {"Germany": 4, "Austria": 1}


@pytest.mark.parametrize(
    "copies, note",
    [
        (
            1,
            "1 incident: 0 at a place, 0 in a country only, 1 at sea only (listed below)",
        ),
        (
            3,
            "3 incidents: 0 at a place, 0 in a country only, 3 at sea only (listed below)",
        ),
    ],
)
def test_events_on_no_map_are_counted(sample_data_dir, copies, note):
    open_sea_rows(copies)
    at = run_app()
    at.multiselect(key="sources").set_value(["leiden"]).run()
    assert not at.exception
    assert note in captions(at)
    assert "on no map" not in at.info[0].value
    assert "At sea only, no country: " in captions(at)


def test_one_located_event_gets_a_singular_split_line(sample_data_dir):
    eodi = read_events("eodi")
    eodi[eodi["lat"].notna()].head(1).to_csv(events_path("eodi"), index=False)
    at = run_app()
    at.multiselect(key="sources").set_value(["eodi"]).run()
    assert "1 incident: 1 at a place (map), 0 in a country only" in captions(at)
    assert "selected events has coordinates" not in captions(at)


def test_no_map_note_is_absent_without_open_sea_events(sample_data_dir):
    at = run_app()
    at.multiselect(key="sources").set_value(["eodi"]).run()
    assert "no country" not in captions(at)
    at.segmented_control[0].set_value("Countries").run()
    assert "no country" not in captions(at)


def test_markers_say_how_many_events_are_at_their_place(sample_data_dir):
    share_places(3)
    at = run_app()
    traces = chart(at, 1).data
    assert all("Events here=" in trace.hovertemplate for trace in traces)
    position = int(
        re.search(r"Events here=%\{customdata\[(\d+)\]", traces[0].hovertemplate)[1]
    )
    on_markers = sorted(row[position] for trace in traces for row in trace.customdata)
    assert on_markers == [1, 1, 1, 1, 3, 3, 3]


def test_caption_counts_events_sharing_their_place(sample_data_dir):
    assert "share their place" not in captions(run_app())
    share_places(3)
    st.cache_data.clear()
    at = run_app()
    assert "3 of the incidents on the map share their place with other events" in (
        captions(at)
    )
    for gone in ("selected events have coordinates", "in the Countries view only"):
        assert gone not in captions(at)
    assert "on no map" not in captions(at)
    at.multiselect(key="statuses").set_value(
        ["confirmed", "unconfirmed", "dismissed"]
    ).run()
    assert "4 of the incidents on the map share their place" in captions(at)
    at.multiselect(key="categories").set_value(["airspace_incursion"]).run()
    assert "at a place (map)" in captions(at)
    assert "share their place" not in captions(at)


def test_status_filter_says_dismissed_events_are_hidden(sample_data_dir):
    at = run_app()
    status = at.multiselect(key="statuses")
    assert status.label == "Event status"
    assert status.help == (
        "Dismissed events (a source or an authority denies that the incident "
        "happened) are hidden by default."
    )
    assert "dismissed" not in status.value


def hover_values(at: AppTest, label: str) -> list:
    traces = chart(at, 1).data
    pattern = rf"{label}=%\{{customdata\[(\d+)\]"
    index = int(re.search(pattern, traces[0].hovertemplate)[1])
    return [row[index] for trace in traces for row in trace.customdata]


def test_the_default_filter_shows_an_incident_with_a_ruled_out_drone(
    sample_data_dir,
):
    row = read_events("eodi").set_index("event_id").loc["eodi:EODI-2025-00112"]
    assert (row["event_status"], row["drone_presence"]) == ("confirmed", "ruled_out")
    at = run_app()
    assert at.multiselect(key="statuses").value == ["confirmed", "unconfirmed"]
    line = line_of(at, VILNIUS_TITLE)
    assert line["status_label"] == "Confirmed"
    assert line["drone_presence_label"] == "Ruled out"


def test_the_table_and_the_hover_show_the_drone_presence(sample_data_dir):
    at = run_app()
    assert "Drone presence" in str(at.dataframe[0].proto.columns)
    table = event_table(at)
    assert {"Ruled out", "Confirmed", "Not confirmed"} <= set(
        table["drone_presence_label"]
    )
    assert (table["drone_presence_label"] == "").any()
    assert "Drone presence=" in chart(at, 1).data[0].hovertemplate
    hovered = set(hover_values(at, "Drone presence"))
    assert {"Ruled out", "Confirmed"} <= hovered
    assert not {"nan", "None"} & {str(value) for value in hovered}


def test_the_app_shows_an_empty_drone_presence_when_the_table_has_no_column(
    sample_data_dir, monkeypatch, tmp_path, downloads
):
    def fetch_old_table(revision):
        path = tmp_path / "old.parquet"
        read_all_events().drop(columns="drone_presence").to_parquet(path)
        return path, REVISION, REVISION_DATE

    monkeypatch.setattr(hub, "fetch", fetch_old_table)
    at = run_app()
    assert set(event_table(at)["drone_presence_label"]) == {""}
    assert set(hover_values(at, "Drone presence")) == {""}
    rows = csv_of(downloads)
    assert "drone_presence" in rows.columns
    assert rows["drone_presence"].isna().all()


def test_country_bar_keeps_the_countries_tied_at_the_cut(sample_data_dir):
    events = read_all_events()
    template = read_events("eodi").iloc[0]
    codes = sorted(EUROPEAN_STATES)[:17]
    # 14 countries with 2 events and 3 with 1: the 3 tie for place 15.
    countries = codes[:14] * 2 + codes[14:]
    tied = pd.DataFrame(
        [
            template.to_dict()
            | {"event_id": f"eodi:T-{n}", "countries": code, "place": code}
            for n, code in enumerate(countries)
        ]
    )
    tied.to_csv(events_path("eodi"), index=False)
    # One Leiden row keeps both sources present without adding a country.
    events[events["event_id"] == "leiden:002"].to_csv(
        events_path("leiden"), index=False
    )
    at = run_app()
    bars = chart(at, -1).data[0]
    assert len(bars.y) == 17
    assert sorted(counts(bars)) == [1] * 3 + [2] * 14
    assert "Most affected countries (top 15, ties kept)" in [
        h.value for h in at.subheader
    ]


MONTH_NOTE = "Some dates are known to the month or year only"


def test_month_precision_note_follows_the_period(sample_data_dir):
    events = read_all_events()
    assert set(events.loc[events["date_precision"] == "month", "source"]) == {"leiden"}
    at = run_app()
    assert MONTH_NOTE not in captions(at)
    at.selectbox(key="period").set_value("Last 12 months").run()
    assert not at.exception
    assert MONTH_NOTE in captions(at)
    assert SOURCES["leiden"].title in captions(at)
    at.selectbox(key="period").set_value("All time").run()
    assert MONTH_NOTE not in captions(at)


def test_month_precision_note_stays_when_the_period_removes_those_events(
    sample_data_dir,
):
    at = run_app()
    at.selectbox(key="period").set_value("Last 30 days").run()
    assert at.info[0].value == "No event matches these filters."
    assert MONTH_NOTE in captions(at)


def test_month_precision_note_needs_a_month_event_among_the_kept_ones(
    sample_data_dir,
):
    at = run_app()
    at.selectbox(key="period").set_value("Last 12 months").run()
    at.multiselect(key="sources").set_value(["eodi"]).run()
    assert not at.exception
    assert MONTH_NOTE not in captions(at)
    at.multiselect(key="sources").set_value(["eodi", "leiden"]).run()
    assert MONTH_NOTE in captions(at)


def test_month_precision_note_follows_date_precision_not_the_source(
    sample_data_dir,
):
    leiden = read_events("leiden")
    leiden["date_precision"] = "day"
    leiden.to_csv(events_path("leiden"), index=False)
    eodi = read_events("eodi")
    eodi.loc[eodi.index[0], "date_precision"] = "month"
    eodi.to_csv(events_path("eodi"), index=False)
    st.cache_data.clear()
    at = run_app()
    at.selectbox(key="period").set_value("Last 12 months").run()
    assert not at.exception
    notes = [c.value for c in at.caption if MONTH_NOTE in c.value]
    assert len(notes) == 1
    assert SOURCES["eodi"].title in notes[0]
    assert SOURCES["leiden"].title not in notes[0]


def test_month_precision_note_shows_for_a_year_date_alone(sample_data_dir):
    leiden = read_events("leiden")
    leiden["date_precision"] = "day"
    leiden.loc[leiden.index[0], "date_precision"] = "year"
    leiden.to_csv(events_path("leiden"), index=False)
    st.cache_data.clear()
    at = run_app()
    at.selectbox(key="period").set_value("Last 12 months").run()
    assert not at.exception
    assert MONTH_NOTE in captions(at)
    assert SOURCES["leiden"].title in captions(at)


def categories_of(domains):
    return [c for c, d in CATEGORY_DOMAINS.items() if d in domains]


@pytest.mark.parametrize("domain", DOMAINS)
def test_adding_a_domain_back_selects_its_categories_again(sample_data_dir, domain):
    at = run_app()
    before = metrics(at)["Incidents"]
    at.multiselect(key="domains").set_value([d for d in DOMAINS if d != domain]).run()
    assert not at.exception
    assert at.multiselect(key="categories").value == categories_of(
        [d for d in DOMAINS if d != domain]
    )
    at.multiselect(key="domains").set_value(DOMAINS).run()
    assert not at.exception
    assert at.multiselect(key="categories").value == list(CATEGORY_DOMAINS)
    assert metrics(at)["Incidents"] == before
    assert not at.warning


def test_two_domain_changes_select_the_categories_of_the_chosen_domains(
    sample_data_dir,
):
    at = run_app()
    at.multiselect(key="domains").set_value(["air", "violence"]).run()
    at.multiselect(key="domains").set_value(["air", "sabotage"]).run()
    assert not at.exception
    assert at.multiselect(key="categories").value == categories_of(["air", "sabotage"])


def test_map_has_points_again_when_air_is_added_back(sample_data_dir):
    at = run_app()
    at.multiselect(key="domains").set_value(["sabotage"]).run()
    at.multiselect(key="domains").set_value(["sabotage", "air"]).run()
    assert not at.exception
    assert not any("has coordinates" in i.value for i in at.info)
    assert {trace.name for trace in chart(at, 1).data} == {"Air"}


def test_narrowed_categories_survive_a_change_of_another_filter(sample_data_dir):
    at = run_app()
    at.multiselect(key="categories").set_value(["airspace_incursion"]).run()
    at.selectbox(key="period").set_value("Last 12 months").run()
    assert not at.exception
    assert at.multiselect(key="categories").value == ["airspace_incursion"]


EODI_TITLE = SOURCES["eodi"].title
LEIDEN_TITLE = SOURCES["leiden"].title


def where_counts(events):
    """Located, Countries-view-only and on-no-map counts of a frame of events."""
    has_coordinates = events["lat"].notna() & events["lon"].notna()
    has_country = events["countries"].notna()
    return (
        int(has_coordinates.sum()),
        int((~has_coordinates & has_country).sum()),
        int((~has_coordinates & ~has_country).sum()),
    )


def test_where_caption_names_the_sources_and_says_the_legend_is_not_a_filter(
    sample_data_dir,
):
    at = run_app()
    shown = expected_events(read_all_events())
    located, countries_only, on_no_map = where_counts(shown)
    assert countries_only > 0
    text = captions(at)
    assert f"Sources with coordinates: {EODI_TITLE}." in text
    assert f"Sources with no coordinates: {LEIDEN_TITLE}." in text
    assert "it is not the Domain filter" in text
    assert (
        f"{len(shown)} incidents: {located} at a place (map), {countries_only} in a "
        f"country only (bars below), {on_no_map} at sea only (listed below)"
    ) in text
    assert located + countries_only + on_no_map == len(shown)
    assert sum(len(trace.customdata) for trace in chart(at, 1).data) == located


def test_where_counts_add_up_with_the_leiden_dataset_alone(sample_data_dir):
    at = run_app()
    at.multiselect(key="sources").set_value(["leiden"]).run()
    assert not at.exception
    shown = expected_events(read_all_events())
    shown = shown[shown["source"] == "leiden"]
    located, countries_only, on_no_map = where_counts(shown)
    assert located == 0
    assert countries_only + on_no_map == len(shown)
    info = at.info[0].value
    assert f"Sources with no coordinates: {LEIDEN_TITLE}." in info
    assert EODI_TITLE not in info
    assert "Countries view" not in info
    assert "have a country and no coordinates" not in info
    assert f"{countries_only} in a country only (bars below)" in captions(at)


def test_where_text_follows_the_sources_and_domain_filters(sample_data_dir):
    at = run_app()
    at.multiselect(key="sources").set_value(["eodi"]).run()
    assert not at.exception
    text = captions(at)
    assert f"Sources with coordinates: {EODI_TITLE}." in text
    assert "Sources with no coordinates" not in text
    shown = expected_events(read_all_events())
    eodi = shown[shown["source"] == "eodi"]
    countries_only = where_counts(eodi)[1]
    assert f"{countries_only} in a country only (bars below)" in text
    at.multiselect(key="sources").set_value(["eodi", "leiden"]).run()
    at.multiselect(key="domains").set_value(["sabotage"]).run()
    assert not at.exception
    info = at.info[0].value
    sabotage = shown[shown["domain"] == "sabotage"]
    assert set(sabotage["source"]) == {"leiden"}
    assert f"Sources with no coordinates: {LEIDEN_TITLE}." in info
    assert EODI_TITLE not in info
    assert where_counts(sabotage)[1] == 1
    assert "a country and no coordinates" not in info
    assert "Try the Countries view" not in info
    assert (
        "1 incident: 0 at a place, 1 in a country only (bars below), "
        "0 at sea only" in captions(at)
    )


def test_category_widget_gets_no_default_and_session_state_value_together(
    sample_data_dir, caplog
):
    importlib.reload(policies)
    caplog.set_level(logging.WARNING, logger=policies.__name__)
    at = run_app()
    at.multiselect(key="domains").set_value(["air"]).run()
    at.multiselect(key="domains").set_value(DOMAINS).run()
    assert not at.exception
    assert "also had its value set via the Session State API" not in caplog.text


def test_footer_gives_the_revision_and_its_date(sample_data_dir):
    at = run_app()
    footer = at.caption[-1].value
    assert hub.REPO_ID in footer
    assert f"revision {REVISION[:7]} of {REVISION_DATE}" in footer
    assert "snapshot" not in footer


def test_the_revision_variable_gives_the_revision_read(
    sample_data_dir, hub_fetches, monkeypatch
):
    run_app()
    assert hub_fetches == ["main"]
    monkeypatch.setenv(hub.REVISION_VARIABLE, "v2.1.0")
    run_app()
    assert hub_fetches == ["main", "v2.1.0"]


def test_the_loader_is_cached_for_one_hour(sample_data_dir, hub_fetches, monkeypatch):
    now = [1_000_000.0]
    monkeypatch.setattr(cache_utils, "TTLCACHE_TIMER", lambda: now[0])
    st.cache_data.clear()
    run_app()
    now[0] += 3599
    run_app()
    assert hub_fetches == ["main"]
    now[0] += 2
    run_app()
    assert hub_fetches == ["main", "main"]


BROGEL_TITLE = (
    "Possible drones fly over Beauvechain military base and other locations in Belgium"
)
VILNIUS_TITLE = "Vilnius airport closure due to suspected balloons"
VILNIUS_EODI = "eodi:EODI-2025-00112"
BROGEL_EODI = "eodi:EODI-2025-00017"
LINKS = {"leiden:V": VILNIUS_EODI, "leiden:K": BROGEL_EODI}
LEIDEN_ONLY = {
    "leiden:V": "Leiden title of the Vilnius incident",
    "leiden:K": "Leiden title of the Brogel incident",
}


def link_incidents(**changes):
    """Write the samples with two incidents that both sources record.

    Vilnius: EODI dismisses it and gives an end date, Leiden confirms it.
    Brogel: EODI says `air` and Belgium, Leiden says `espionage`, Belgium and
    Sweden. `changes` maps an `event_id` to the fields to set on that row.
    """
    eodi, leiden = (read_events(name) for name in SOURCES)
    for table in (eodi, leiden):
        for column in ("date", "end_date"):
            stamps = pd.to_datetime(table[column])
            table[column] = stamps.dt.strftime("%Y-%m-%d").astype(object)
    template = leiden.iloc[0].to_dict() | {
        "countries": "BE",
        "geo_precision": "country",
        "domain": "espionage",
        "category": "reconnaissance",
        "event_status": "confirmed",
        "actors": "RU",
        "attribution": "assessed",
    }
    added = [
        template
        | {
            "event_id": "leiden:V",
            "date": "2025-10-01",
            "end_date": "2025-12-31",
            "countries": "LT",
            "title": LEIDEN_ONLY["leiden:V"],
        },
        template
        | {
            "event_id": "leiden:K",
            "date": "2025-11-01",
            "countries": "BE;SE",
            "title": LEIDEN_ONLY["leiden:K"],
        },
    ]
    leiden = pd.concat([leiden, pd.DataFrame(added)], ignore_index=True)
    eodi.loc[eodi["event_id"] == VILNIUS_EODI, "end_date"] = "2025-10-05"
    eodi.loc[eodi["event_id"] == VILNIUS_EODI, "event_status"] = "dismissed"
    both = pd.concat([eodi, leiden], ignore_index=True)
    both["cluster_id"] = both["event_id"].map(LINKS).fillna(both["event_id"])
    both["n_sources"] = both.groupby("cluster_id")["source"].transform("nunique")
    both["is_canonical"] = both["source"] == "eodi"
    for event_id, fields in changes.items():
        for column, value in fields.items():
            both.loc[both["event_id"] == event_id, column] = value
    for name in SOURCES:
        both[both["source"] == name].to_csv(events_path(name), index=False)


@pytest.fixture
def downloads(monkeypatch):
    """The CSV texts that the app gave to `st.download_button`, latest last."""
    given = []

    def record(label, data, **kwargs):
        given.append(data)
        return False

    monkeypatch.setattr(st, "download_button", record)
    return given


def csv_of(downloads) -> pd.DataFrame:
    return read_csv(io.StringIO(downloads[-1]))


def event_table(at: AppTest) -> pd.DataFrame:
    return at.dataframe[0].value.astype(str)


def line_of(at: AppTest, title: str) -> dict:
    table = event_table(at)
    found = table[table["title"] == title]
    assert len(found) == 1, title
    return found.iloc[0].to_dict()


def numbers(array) -> list:
    """Values of a Plotly array, which Plotly 7 sends as base64 when numeric."""
    if isinstance(array, dict):
        return np.frombuffer(
            base64.b64decode(array["bdata"]), dtype=array["dtype"]
        ).tolist()
    return list(array)


def by_domain(at: AppTest) -> dict[str, int]:
    """Incidents of the timeline, by domain."""
    return {trace.name: int(sum(numbers(trace.y))) for trace in chart(at, 0).data}


def app_state(at: AppTest, downloads: list[str] | None = None) -> dict:
    """Figures, charts, table values, captions and CSV that a filter can change."""
    return {
        "figures": metrics(at),
        "charts": [item.proto.spec for item in at.get("plotly_chart")],
        "table": event_table(at).to_dict("list") if at.dataframe else None,
        "columns": str(at.dataframe[0].proto.columns) if at.dataframe else None,
        "links": (
            at.dataframe[0].value.filter(like="link_").to_dict("list")
            if at.dataframe
            else None
        ),
        "texts": captions(at) + " ".join(info.value for info in at.info),
        "csv": downloads[-1] if downloads else None,
    }


def test_one_line_for_each_incident(sample_data_dir, downloads):
    link_incidents()
    at = run_app()
    rows = read_all_events()
    expected = rows[rows["event_status"] != "dismissed"]["event_id"].map(
        lambda event_id: LINKS.get(event_id, event_id)
    )
    assert metrics(at)["Incidents"] == str(len(event_table(at)))
    assert len(event_table(at)) == csv_of(downloads)["cluster_id"].nunique()
    assert len(event_table(at)) >= expected.nunique()


def test_the_line_of_two_sources_shows_the_value_of_each(sample_data_dir):
    link_incidents()
    line = line_of(run_app(), BROGEL_TITLE)
    assert line["domain_label"] == "Air (EODI); Espionage (Leiden)"
    assert line["category_label"] == (
        "Drone overflight (EODI); Reconnaissance (Leiden)"
    )
    assert line["status_label"] == "Reported, not confirmed (EODI); Confirmed (Leiden)"
    assert line["drone_presence_label"] == "Not confirmed (EODI); — (Leiden)"
    assert line["attribution_label"] == (
        "No attribution in the source (EODI); "
        "Assessed by the source's analysts (Leiden)"
    )
    assert line["actor_names"] == "— (EODI); Russian Federation (Leiden)"
    assert line["country_names"] == "Belgium (EODI); Belgium, Sweden (Leiden)"


def test_a_line_that_the_sources_agree_on_shows_one_value(sample_data_dir):
    link_incidents(**{"leiden:K": {"domain": "air"}})
    assert line_of(run_app(), BROGEL_TITLE)["domain_label"] == "Air"


def test_an_incident_counts_in_the_domain_of_each_source(sample_data_dir):
    link_incidents()
    at = run_app()
    totals = by_domain(at)
    assert totals["Espionage"] == 3
    assert totals["Air"] > 0
    assert sum(totals.values()) > int(metrics(at)["Incidents"])
    assert "can add up to more than the number of incidents" in captions(at)


def test_the_domain_filter_leaves_the_line_unchanged(sample_data_dir):
    link_incidents()
    at = run_app()
    before = line_of(at, BROGEL_TITLE)
    at.multiselect(key="domains").set_value(["espionage"]).run()
    assert not at.exception
    assert line_of(at, BROGEL_TITLE) == before
    assert by_domain(at) == {"Espionage": 3}
    assert metrics(at)["Incidents"] == "3"


def test_the_source_filter_removes_the_rows_of_the_other_sources(
    sample_data_dir, downloads
):
    link_incidents()
    at = run_app()
    at.multiselect(key="sources").set_value(["leiden"]).run()
    assert not at.exception
    table = event_table(at)
    assert metrics(at)["Incidents"] == str(len(table))
    assert not table.apply(lambda column: column.str.contains("EODI")).to_numpy().any()
    assert not table["title"].str.contains("Vilnius airport").any()
    assert set(csv_of(downloads)["source"]) == {"leiden"}
    assert by_domain(at)["Air"] == 1
    at.multiselect(key="sources").set_value(["eodi"]).run()
    assert not at.exception
    table = event_table(at)
    assert metrics(at)["Incidents"] == str(len(table))
    assert (
        not table.apply(lambda column: column.str.contains("Leiden")).to_numpy().any()
    )
    assert set(csv_of(downloads)["source"]) == {"eodi"}
    assert set(by_domain(at)) == {"Air"}


def test_the_line_takes_its_dates_from_the_first_source_in_scope(sample_data_dir):
    link_incidents()
    at = run_app()
    line = line_of(at, VILNIUS_TITLE)
    assert line["date"].startswith("2025-10-04")
    assert line["end_date"].startswith("2025-10-05")
    at.multiselect(key="sources").set_value(["leiden"]).run()
    line = line_of(at, LEIDEN_ONLY["leiden:V"])
    assert line["date"].startswith("2025-10-01")
    assert line["end_date"].startswith("2025-12-31")


def test_the_status_of_each_source_shows_and_a_dismissed_filter_changes_nothing(
    sample_data_dir,
):
    link_incidents()
    at = run_app()
    line = line_of(at, VILNIUS_TITLE)
    assert line["status_label"] == "Dismissed by the source (EODI); Confirmed (Leiden)"
    at.multiselect(key="statuses").set_value(
        ["confirmed", "unconfirmed", "dismissed"]
    ).run()
    assert not at.exception
    assert line_of(at, VILNIUS_TITLE) == line


def test_a_filter_tests_its_conditions_on_the_same_row_of_the_incident(
    sample_data_dir,
):
    link_incidents()
    at = run_app()
    at.multiselect(key="domains").set_value(["air"]).run()
    at.multiselect(key="statuses").set_value(["confirmed"]).run()
    assert not at.exception
    titles = set(event_table(at)["title"])
    assert VILNIUS_TITLE not in titles
    assert BROGEL_TITLE not in titles
    assert "Drones over Hanover airport force flight diversions" in titles


def test_the_countries_filter_shows_the_countries_of_each_source(sample_data_dir):
    link_incidents()
    at = run_app()
    at.multiselect(key="countries").set_value(["SE"]).run()
    assert not at.exception
    assert line_of(at, BROGEL_TITLE)["country_names"] == (
        "Belgium (EODI); Belgium, Sweden (Leiden)"
    )
    assert metrics(at)["Countries"] == "1"
    assert metrics(at)["Incidents"] == "1"


def test_the_download_holds_the_rows_in_scope_of_the_listed_incidents(
    sample_data_dir, downloads
):
    link_incidents()
    at = run_app()
    rows = csv_of(downloads)
    assert list(rows.columns) == PUBLISHED_COLUMNS
    brogel = rows[rows["cluster_id"] == BROGEL_EODI].set_index("source")
    assert brogel.loc["eodi", "domain"] == "air"
    assert brogel.loc["leiden", "domain"] == "espionage"
    assert brogel.loc["leiden", "countries"] == "BE;SE"
    at.multiselect(key="domains").set_value(["espionage"]).run()
    rows = csv_of(downloads)
    assert set(rows["cluster_id"]) == {BROGEL_EODI, VILNIUS_EODI, "leiden:002"}
    assert len(rows[rows["cluster_id"] == VILNIUS_EODI]) == 2


def test_the_map_has_one_marker_for_each_domain_of_an_incident(sample_data_dir):
    link_incidents()
    at = run_app()
    eodi = read_events("eodi").set_index("event_id")
    place = (eodi.loc[BROGEL_EODI, "lat"], eodi.loc[BROGEL_EODI, "lon"])
    traces = {trace.name: trace for trace in chart(at, 1).data}
    for name in ("Air", "Espionage"):
        points = list(
            zip(numbers(traces[name].lat), numbers(traces[name].lon), strict=True)
        )
        assert sum(point == place for point in points) == 1, name


def test_the_timeline_counts_an_incident_in_the_month_of_its_line(sample_data_dir):
    link_incidents(**{"leiden:V": {"date": "2025-09-01"}})
    at = run_app()
    espionage = next(t for t in chart(at, 0).data if t.name == "Espionage")
    months = {str(month)[:7] for month in espionage.x}
    assert "2025-10" in months
    assert "2025-09" not in months


def test_last_30_days_tests_the_date_of_the_line(
    sample_data_dir,
):
    recent = (utc_today() - pd.Timedelta(days=5)).strftime("%Y-%m-%d")
    old = (utc_today() - pd.Timedelta(days=100)).strftime("%Y-%m-%d")
    link_incidents(**{BROGEL_EODI: {"date": recent}, "leiden:K": {"date": old}})
    at = run_app()
    assert metrics(at)["Last 30 days"] == "1"
    at.selectbox(key="period").set_value("Last 90 days").run()
    assert not at.exception
    assert metrics(at)["Last 30 days"] == "1"
    at.selectbox(key="period").set_value("All time").run()
    at.multiselect(key="sources").set_value(["leiden"]).run()
    assert metrics(at)["Last 30 days"] == "0"


def test_the_period_filter_tests_the_date_of_the_line(sample_data_dir):
    recent = (utc_today() - pd.Timedelta(days=5)).strftime("%Y-%m-%d")
    old = (utc_today() - pd.Timedelta(days=500)).strftime("%Y-%m-%d")
    link_incidents(**{BROGEL_EODI: {"date": old}, "leiden:K": {"date": recent}})
    at = run_app()
    at.selectbox(key="period").set_value("Last 12 months").run()
    assert not at.exception
    assert BROGEL_TITLE not in set(event_table(at)["title"])
    at.selectbox(key="period").set_value("All time").run()
    assert BROGEL_TITLE in set(event_table(at)["title"])


def test_the_official_count_takes_the_states_from_the_official_rows(sample_data_dir):
    link_incidents(
        **{
            "leiden:K": {"attribution": "official", "actors": "RU;BY"},
            BROGEL_EODI: {"attribution": "none", "actors": ""},
        }
    )
    at = run_app()
    kpi = next(m for m in at.metric if m.label == "Officially attributed")
    assert "Belarus 1" in kpi.help
    assert "Russian Federation 2" in kpi.help


@pytest.mark.parametrize(
    ("key", "changed"),
    [
        ("period", "Last 12 months"),
        ("sources", ["leiden"]),
        ("domains", ["espionage"]),
        ("categories", ["reconnaissance"]),
        ("actors", ["RU"]),
        ("attributions", ["assessed"]),
        ("statuses", ["unconfirmed"]),
        ("countries", ["SE"]),
    ],
)
def test_a_change_of_a_filter_and_its_undo_restore_the_whole_state(
    sample_data_dir, downloads, key, changed
):
    link_incidents()
    at = run_app()
    before = app_state(at, downloads)
    widget = at.selectbox(key=key) if key == "period" else at.multiselect(key=key)
    original = widget.value
    widget.set_value(changed).run()
    assert not at.exception
    assert app_state(at, downloads) != before, key
    widget = at.selectbox(key=key) if key == "period" else at.multiselect(key=key)
    widget.set_value(original).run()
    assert not at.exception
    assert app_state(at, downloads) == before


def link_columns(at: AppTest) -> dict[str, str]:
    """The link columns of the table, by column id, with their headers."""
    columns = json.loads(str(at.dataframe[0].proto.columns))
    return {
        name: config["label"]
        for name, config in columns.items()
        if name.startswith("link_")
    }


def links_of(at: AppTest, title: str) -> dict[str, object]:
    table = at.dataframe[0].value
    row = table[table["title"] == title].iloc[0]
    return {name: row[name] for name in link_columns(at)}


def test_the_table_has_a_link_column_for_each_selected_source(sample_data_dir):
    link_incidents()
    at = run_app()
    default = app_state(at)
    rows = read_all_events().set_index("event_id")
    assert "url" not in at.dataframe[0].value.columns
    assert link_columns(at) == {"link_eodi": "EODI", "link_leiden": "Leiden"}
    assert list(link_columns(at)) == ["link_eodi", "link_leiden"]
    assert list(at.dataframe[0].value.columns)[-2:] == ["link_eodi", "link_leiden"]
    assert links_of(at, BROGEL_TITLE) == {
        "link_eodi": rows.loc[BROGEL_EODI, "url"],
        "link_leiden": rows.loc["leiden:K", "url"],
    }
    hanover = "Drones over Hanover airport force flight diversions"
    assert links_of(at, hanover) == {
        "link_eodi": rows.loc["eodi:EODI-2025-00001", "url"],
        "link_leiden": "",
    }
    at.multiselect(key="sources").set_value(["leiden"]).run()
    assert not at.exception
    assert link_columns(at) == {"link_leiden": "Leiden"}
    assert links_of(at, LEIDEN_ONLY["leiden:K"]) == {
        "link_leiden": rows.loc["leiden:K", "url"]
    }
    at.multiselect(key="sources").set_value(["eodi"]).run()
    assert not at.exception
    assert link_columns(at) == {"link_eodi": "EODI"}
    assert links_of(at, BROGEL_TITLE) == {"link_eodi": rows.loc[BROGEL_EODI, "url"]}
    at.multiselect(key="sources").set_value(list(SOURCES)).run()
    assert not at.exception
    assert app_state(at) == default


def test_an_empty_value_shows_as_a_dash_and_its_source_when_the_rows_disagree(
    sample_data_dir,
):
    link_incidents()
    line = line_of(run_app(), BROGEL_TITLE)
    assert line["actor_names"] == "— (EODI); Russian Federation (Leiden)"


def test_a_value_that_every_row_leaves_empty_stays_empty(sample_data_dir):
    link_incidents(**{"leiden:K": {"actors": "", "attribution": "none"}})
    line = line_of(run_app(), BROGEL_TITLE)
    assert line["actor_names"] == ""


def quality_rows(**dates):
    """Quality rows of two runs: the older run has dates that must not show."""
    rows = [
        {
            "run_at": "2026-11-25T03:00:00+00:00",
            "source": name,
            "downloaded_at": "2026-11-25T02:00:00+00:00",
            "updated_by_source": "2026-11-01",
        }
        for name in SOURCES
    ]
    rows += [
        {"run_at": "2026-11-26T03:00:00+00:00", "source": name} | values
        for name, values in dates.items()
    ]
    return pd.DataFrame(rows)


def test_sources_show_the_two_dates_and_blank_cells(sample_data_dir, monkeypatch):
    quality = quality_rows(
        eodi={
            "downloaded_at": "2026-11-26T02:15:42.123456+00:00",
            "updated_by_source": "2026-11-20T08:30:15Z",
        },
        leiden={
            "downloaded_at": "2026-11-26T02:16:00+01:00",
            "updated_by_source": None,
        },
    )
    monkeypatch.setattr(hub, "fetch_quality", lambda commit: quality)
    at = run_app()
    table = at.dataframe[-1].value.set_index("Source")
    config = json.loads(str(at.dataframe[-1].proto.columns))
    assert config["Downloaded"]["help"] == (
        "Time of the download whose data this table shows."
    )
    assert config["Updated by source"]["help"] == (
        "Date of the last change that the source gives. "
        "Blank: the source gives no date."
    )
    assert list(table.columns[:5]) == [
        "Update",
        "Events",
        "Downloaded",
        "Updated by source",
        "From",
    ]
    eodi, leiden = SOURCES["eodi"].title, SOURCES["leiden"].title
    assert table.loc[eodi, "Downloaded"] == "2026-11-26 02:15 UTC"
    assert table.loc[eodi, "Updated by source"] == "2026-11-20 08:30 UTC"
    assert table.loc[leiden, "Downloaded"] == "2026-11-26 01:16 UTC"
    assert table.loc[leiden, "Updated by source"] == ""
    assert table.loc[eodi, "Update"] == "Daily refresh"
    assert "None" not in table.astype(str).to_string()


def test_sources_with_no_quality_table_show_empty_cells(sample_data_dir):
    at = run_app()
    table = at.dataframe[-1].value
    assert (table["Downloaded"] == "").all()
    assert (table["Updated by source"] == "").all()
    assert len(table) == len(SOURCES)


def drop_source(name: str) -> None:
    """Publish no row of source `name`."""
    read_events(name).iloc[0:0].to_csv(events_path(name), index=False)


def test_a_source_with_no_published_row_is_not_named(sample_data_dir):
    drop_source("leiden")
    at = run_app()
    assert at.multiselect(key="sources").options == [SOURCES["eodi"].title]
    assert at.multiselect(key="sources").value == ["eodi"]
    assert list(at.dataframe[-1].value["Source"]) == [SOURCES["eodi"].title]
    assert SOURCES["eodi"].citation in captions(at)
    assert SOURCES["leiden"].citation not in captions(at)


def test_the_events_table_has_no_link_column_for_a_source_with_no_published_row(
    sample_data_dir,
):
    drop_source("leiden")
    assert link_columns(run_app()) == {"link_eodi": "EODI"}


def serve_at(monkeypatch, name: str, day: int, files: dict[str, bytes]) -> None:
    """Make source `name` download `files` on day `day` of the test run."""
    now = pd.Timestamp("2026-03-05 10:00:00", tz="UTC") + pd.Timedelta(days=day)
    monkeypatch.setattr(clock, "utc_now", lambda: now)
    monkeypatch.setitem(
        SOURCES,
        name,
        dataclasses.replace(
            SOURCES[name],
            extract=lambda: [
                Payload(name, file, content, f"https://e/{file}", 200)
                for file, content in files.items()
            ],
        ),
    )


def test_a_source_that_keeps_its_last_data_shows_the_dates_of_its_accepted_download(
    tmp_path, monkeypatch
):
    store = Store(LocalBackend(tmp_path / "store"))
    store.save(
        Payload("geonames", geonames.FILE_NAME, geonames_archive(), geonames.URL, 200)
    )
    actors_file = (FIXTURES / "misp_threat_actor.json").read_bytes()
    store.save(Payload(actors.SOURCE, actors.FILE_NAME, actors_file, actors.URL, 200))
    curation = tmp_path / "curation"
    curation.mkdir()
    for name in ("merges.csv", "splits.csv"):
        (curation / name).write_text("event_id_a,event_id_b,reason\n")
    (curation / "actors.csv").write_text(",".join(actors.CURATION_COLUMNS) + "\n")
    good = recorded_payloads("leiden")
    good["version.json"] = (FIXTURES / "leiden_dataverse_version.json").read_bytes()
    version = json.loads(good["version.json"])
    version["data"]["releaseTime"] = "2026-03-06T08:00:00Z"
    malformed = {
        "eventdata.xlsx": b"malformed",
        "version.json": json.dumps(version).encode(),
    }
    for name, files in (("leiden", good), ("eodi", recorded_payloads("eodi"))):
        serve_at(monkeypatch, name, 0, files)
        ingest.ingest(name, store)
    serve_at(monkeypatch, "leiden", 1, malformed)
    with pytest.raises(zipfile.BadZipFile):
        ingest.ingest("leiden", store)
    serve_at(monkeypatch, "eodi", 1, recorded_payloads("eodi"))
    ingest.ingest("eodi", store)
    build.build(store, tmp_path / "out", curation_dir=curation)
    quality.run(store, tmp_path / "out")
    family = tmp_path / "out" / "open"
    published = pd.read_parquet(family / "events.parquet")
    monkeypatch.setattr(
        hub, "fetch", lambda revision: (family / "events.parquet", REVISION, "")
    )
    monkeypatch.setattr(
        hub, "fetch_quality", lambda commit: pd.read_parquet(family / "quality.parquet")
    )
    table = run_app().dataframe[-1].value.set_index("Source")
    leiden = table.loc[SOURCES["leiden"].title]
    assert leiden["Events"] == (published["source"] == "leiden").sum() > 0
    assert leiden["Downloaded"] == "2026-03-05 10:00 UTC"
    assert leiden["Updated by source"] == "2026-01-13 14:22 UTC"
    assert table.loc[SOURCES["eodi"].title, "Downloaded"] == "2026-03-06 10:00 UTC"


def test_the_split_line_and_the_line_under_the_map(sample_data_dir):
    at = run_app()
    assert (
        "18 incidents: 8 at a place (map), 9 in a country only (bars below), "
        "1 at sea only (listed below)" in captions(at)
    )
    assert "At sea only, no country: North Sea; Baltic Sea (1)." in captions(at)
    assert sum(len(trace.customdata) for trace in chart(at, 1).data) == 8


def test_the_split_line_for_a_domain_and_a_country(sample_data_dir):
    at = run_app()
    at.multiselect(key="domains").set_value(["espionage"]).run()
    assert not at.exception
    assert (
        "1 incident: 0 at a place, 0 in a country only, 1 at sea only (listed below)"
        in captions(at)
    )
    assert "At sea only, no country: North Sea; Baltic Sea (1)." in captions(at)
    assert "on no map" not in at.info[0].value
    at.multiselect(key="domains").set_value(DOMAINS).run()
    at.multiselect(key="countries").set_value(["DE"]).run()
    assert (
        "4 incidents: 1 at a place (map), 3 in a country only (bars below), "
        "0 at sea only" in captions(at)
    )
    assert "At sea only" not in captions(at)


def test_the_message_without_a_map_names_the_sources_without_coordinates(
    sample_data_dir,
):
    at = run_app()
    at.multiselect(key="domains").set_value(["sabotage"]).run()
    info = at.info[0].value
    assert "Russian Operations Against Europe Dataset, 2022-2025" in info
    assert "Countries view" not in info
    assert "a country and no coordinates" not in info


def hover_point(at):
    traces = chart(at, 1).data
    position = int(
        re.search(r"Point=%\{customdata\[(\d+)\]", traces[0].hovertemplate)[1]
    )
    return {row[position] for trace in traces for row in trace.customdata}


def test_the_hover_says_where_the_point_comes_from(sample_data_dir):
    assert hover_point(run_app()) == {"Coordinates from the source"}
    eodi = read_events("eodi")
    located = eodi[eodi["lat"].notna()]
    eodi.loc[located.index[0], "geo_precision"] = "place"
    eodi.to_csv(events_path("eodi"), index=False)
    st.cache_data.clear()
    assert "Town centre (GeoNames) of the place that the source names" in hover_point(
        run_app()
    )


HATCH_KEY = "Hatched: also names other countries"
BARS_TITLE = "incidents in a country only (no point on the map)"
BARS_LIMIT = (
    "Incidents with a country and no point on the map, states of the European "
    "list only. An incident counts once in each country and each domain that "
    "its sources give it."
)
DOMAIN_COLORS = {
    "light": {
        "Air": "#0072b2",
        "Sabotage": "#d55e00",
        "Violence": "#7a1f5c",
        "Information": "#c99700",
    },
    "dark": {
        "Air": "#2f7fd0",
        "Sabotage": "#e8742a",
        "Violence": "#b0558f",
        "Information": "#f0c040",
    },
}


def bars_chart(at: AppTest):
    for index in range(len(at.get("plotly_chart"))):
        fig = chart(at, index)
        if any(trace.name == HATCH_KEY for trace in fig.data):
            return fig
    return None


def bar_segments(fig) -> dict[tuple[str, str], tuple[int, int]]:
    """Solid and hatched count of each (country, domain) segment of the bars."""
    found: dict[tuple[str, str], list[int]] = {}
    for trace in fig.data:
        if trace.name not in DOMAIN_COLORS["light"] or not trace.y:
            continue
        for country, count in zip(trace.y, trace.x):
            if count:
                pair = found.setdefault((country, trace.name), [0, 0])
                pair[1 if trace.marker.pattern.shape else 0] += int(count)
    return {key: (solid, hatched) for key, (solid, hatched) in found.items()}


def bars_title(at: AppTest) -> list[str]:
    return [m.value for m in at.markdown if "in a country only (no point" in m.value]


def test_the_bars_count_the_incidents_in_a_country_only(sample_data_dir):
    at = run_app()
    fig = bars_chart(at)
    found = bar_segments(fig)
    countries = {country for country, _ in found}
    assert len(countries) == 19
    assert countries.isdisjoint({"United States", "Ukraine"})
    assert sum(found[key][0] for key in found if key[0] == "Germany") == 0
    assert found[("Germany", "Information")] == (0, 3)
    assert {key: found[key] for key in found if key[0] == "Latvia"} == {
        ("Latvia", "Air"): (0, 1),
        ("Latvia", "Information"): (0, 1),
    }
    assert bars_title(at) == [f"##### 9 {BARS_TITLE}"]
    text = captions(at)
    assert BARS_LIMIT in text
    assert "The bars add up to 24, more than the 9 incidents." in text
    assert "not counted here: Ukraine (1), United States (1)." in text
    assert (
        "18 incidents: 8 at a place (map), 9 in a country only (bars below), "
        "1 at sea only (listed below)" in text
    )


def test_the_bars_come_under_the_map_at_full_width(sample_data_dir):
    at = run_app()
    order = [
        (
            "bars"
            if element.type == "markdown" and element.value == f"##### 9 {BARS_TITLE}"
            else element.type
        )
        for element in at.main
    ]
    where = order.index("bars")
    assert order[where - 2 : where + 5] == [
        "plotly_chart",
        "caption",
        "bars",
        "plotly_chart",
        "caption",
        "caption",
        "subheader",
    ]
    map_figure = chart(at, 1)
    assert any(len(trace.customdata or []) for trace in map_figure.data)
    assert all(trace.type != "bar" for trace in map_figure.data)


def test_the_hatch_marks_the_incidents_that_name_several_countries(sample_data_dir):
    at = run_app()
    fig = bars_chart(at)
    found = bar_segments(fig)
    assert found[("Austria", "Information")] == (0, 1)
    assert found[("Germany", "Information")] == (0, 3)
    assert found[("Estonia", "Information")] == (1, 0)
    assert found[("Bulgaria", "Sabotage")] == (1, 0)
    assert found[("Bulgaria", "Air")] == (0, 1)
    germany = next(
        trace.customdata[list(trace.y).index("Germany")]
        for trace in fig.data
        if trace.name == "Information" and trace.marker.pattern.shape
    )
    assert "3 incidents: 0 name only Germany, 3 also name other countries" in germany
    assert "4 of these 9 incidents name several countries" in captions(at)
    assert fig.layout.xaxis.type == "linear"
    assert fig.layout.xaxis.rangemode == "tozero"


def test_the_bars_legend_has_one_entry_for_each_domain_and_one_for_the_hatch(
    sample_data_dir,
):
    fig = bars_chart(run_app())
    domains = ["Air", "Sabotage", "Violence", "Information"]
    entries = [trace.name for trace in fig.data if trace.showlegend is not False]
    assert entries == [*domains, HATCH_KEY]
    for domain in domains:
        solid, hatched = [trace for trace in fig.data if trace.name == domain]
        assert solid.showlegend and not solid.marker.pattern.shape
        assert hatched.marker.pattern.shape
        assert hatched.legendgroup == solid.legendgroup == domain
        assert hatched.showlegend is False


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_each_segment_has_the_colour_of_its_domain_in_the_theme(sample_data_dir, theme):
    at = AppTest.from_file(str(APP), default_timeout=30)
    at.context["theme"] = theme
    at.run()
    assert not at.exception
    fig = bars_chart(at)
    surface = "#0e1117" if theme == "dark" else "#ffffff"
    traces = [trace for trace in fig.data if trace.name in DOMAIN_COLORS[theme]]
    assert len(traces) == 8
    for trace in traces:
        colour = DOMAIN_COLORS[theme][trace.name]
        assert trace.marker.color == colour
        assert trace.marker.line.color == surface
        if trace.marker.pattern.shape:
            assert trace.marker.pattern.bgcolor == colour
            assert trace.marker.pattern.fgcolor == surface


def test_the_bars_follow_the_domain_filter(sample_data_dir):
    at = run_app()
    at.multiselect(key="domains").set_value(["sabotage"]).run()
    assert not at.exception
    assert bar_segments(bars_chart(at)) == {("Bulgaria", "Sabotage"): (1, 0)}
    assert "Countries view" not in at.info[0].value
    at.multiselect(key="domains").set_value(["espionage"]).run()
    assert not at.exception
    assert not bars_title(at)
    assert BARS_LIMIT not in captions(at)
    assert bars_chart(at) is None


def test_the_bars_follow_the_countries_filter(sample_data_dir):
    at = run_app()
    at.multiselect(key="countries").set_value(["DE"]).run()
    assert not at.exception
    assert bar_segments(bars_chart(at)) == {("Germany", "Information"): (0, 3)}
    assert "The bars add up" not in captions(at)
    at.multiselect(key="countries").set_value(["US"]).run()
    assert not at.exception
    text = captions(at)
    assert "1 incident: 0 at a place, 1 in a country only, 0 at sea only" in text
    assert "bars below" not in text
    assert bars_title(at) == [f"##### 1 {BARS_TITLE.replace('incidents', 'incident')}"]
    assert bars_chart(at) is None
    assert BARS_LIMIT in text
    assert "United States (1)" in text
    assert "Ukraine" not in text
    assert "name several countries" not in text


def test_the_countries_view_has_no_bars(sample_data_dir):
    at = run_app()
    at.segmented_control[0].set_value("Countries").run()
    assert not at.exception
    assert len(at.get("plotly_chart")) == 3
    assert not bars_title(at)
    assert BARS_LIMIT not in captions(at)
    assert "bars below" not in captions(at)


def test_most_affected_countries_says_that_it_counts_the_incidents_with_no_point(
    sample_data_dir,
):
    assert (
        "Counts every selected incident that names the country, with or without "
        "a point on the map."
    ) in captions(run_app())


CONTEXT_TITLE = "Fake context"
CONTEXT_CITATION = "Source: Fake context."


def add_context_source(monkeypatch, tmp_path) -> None:
    """Register a `nc` context source and serve two month files from the Hub.

    The oldest file starts on the 30th, not on the 1st, and the newest ends on
    the 3rd: From and To must come from the days in the files.
    """
    monkeypatch.setitem(
        SOURCES,
        stanford.SOURCE,
        dataclasses.replace(
            SOURCES["eodi"],
            title=CONTEXT_TITLE,
            citation=CONTEXT_CITATION,
            licence="CC BY-NC 4.0",
            family="nc",
            table="context",
        ),
    )
    read_events("eodi").iloc[0:0].to_csv(events_path(stanford.SOURCE), index=False)
    heatmap = (FIXTURES / "stanford_heatmap.json").read_bytes()
    files = {}
    for month, days in (("2026-10", (30, 31)), ("2026-11", (2, 3))):
        table = pd.concat(
            [
                stanford.reduce_day(date(*map(int, month.split("-")), day), heatmap)
                for day in days
            ]
        )
        path = tmp_path / f"{month}.parquet"
        table.to_parquet(path, index=False)
        files[f"{stanford.TABLE}/{month}.parquet"] = path
    monkeypatch.setattr(hub, "fetch_month_ends", lambda commit, folder: files)


def sources_sent(at: AppTest) -> tuple[pa.Schema, str | None]:
    """The Arrow schema of the Sources section, as the app sends it to the
    browser, and the text that the browser draws for a missing value (None:
    the Streamlit default, "None")."""
    proto = at.dataframe[-1].proto
    schema = pa.ipc.open_stream(proto.arrow_data.data).schema
    return schema, proto.placeholder if proto.HasField("placeholder") else None


@pytest.mark.allow_hosts(["127.0.0.1"])
def test_the_sources_section_lists_a_context_source_with_a_blank_events_cell(
    sample_data_dir, monkeypatch, tmp_path
):
    add_context_source(monkeypatch, tmp_path)
    at = run_app()

    table = at.dataframe[-1].value.set_index("Source")
    schema, placeholder = sources_sent(at)
    assert list(table.index) == [
        SOURCES["eodi"].title,
        SOURCES["leiden"].title,
        CONTEXT_TITLE,
    ]
    assert pa.types.is_integer(schema.field("Events").type)
    assert placeholder == ""
    assert table.loc[SOURCES["eodi"].title, "Events"] == len(read_events("eodi"))
    assert pd.isna(table.loc[CONTEXT_TITLE, "Events"])
    assert str(table.loc[CONTEXT_TITLE, "From"]) == "2026-10-30"
    assert str(table.loc[CONTEXT_TITLE, "To"]) == "2026-11-03"
    assert CONTEXT_CITATION in captions(at)
    assert at.multiselect(key="sources").options == [
        SOURCES["eodi"].title,
        SOURCES["leiden"].title,
    ]
    assert at.multiselect(key="sources").value == ["eodi", "leiden"]
    assert link_columns(at) == {"link_eodi": "EODI", "link_leiden": "Leiden"}


def refuse(*args):
    raise ConnectionError("no network")


@pytest.mark.allow_hosts(["127.0.0.1"])
@pytest.mark.parametrize("cause", ["read error", "snapshot"])
def test_a_context_source_whose_month_files_cannot_be_read_shows_blank_days(
    sample_data_dir, monkeypatch, tmp_path, cause
):
    add_context_source(monkeypatch, tmp_path)
    if cause == "snapshot":
        folder = tmp_path / "snapshot"
        folder.mkdir()
        read_all_events().to_parquet(folder / hub.EVENTS_FILE)
        monkeypatch.setenv(hub.SNAPSHOT_VARIABLE, str(folder))
        monkeypatch.setattr(hub, "fetch", refuse)
    else:
        monkeypatch.setattr(hub, "fetch_month_ends", refuse)
    at = run_app()

    table = at.dataframe[-1].value.set_index("Source")
    assert list(table.index)[-1] == CONTEXT_TITLE
    assert table.loc[CONTEXT_TITLE, ["Events", "From", "To"]].isna().all()
    assert sources_sent(at)[1] == ""
    assert CONTEXT_CITATION in captions(at)
    assert ("showing the snapshot" in captions(at)) == (cause == "snapshot")


@pytest.mark.allow_hosts(["127.0.0.1"])
def test_a_context_source_with_no_month_file_on_the_hub_is_not_listed(
    sample_data_dir, monkeypatch, tmp_path
):
    add_context_source(monkeypatch, tmp_path)
    monkeypatch.setattr(hub, "fetch_month_ends", lambda commit, folder: {})
    at = run_app()

    assert CONTEXT_TITLE not in set(at.dataframe[-1].value["Source"])
    assert CONTEXT_CITATION not in captions(at)
