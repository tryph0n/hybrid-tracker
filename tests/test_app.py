"""The dashboard run headless with AppTest, on samples."""

import base64
import importlib
import logging
import re

import numpy as np
import pandas as pd
import plotly.io as pio
import pytest
import streamlit as st
from conftest import (
    APP,
    REVISION,
    REVISION_DATE,
    events_path,
    read_all_events,
    read_events,
)
from streamlit.elements.lib import policies
from streamlit.runtime.caching import cache_utils
from streamlit.testing.v1 import AppTest

from tracker import hub
from tracker.schema import ATTRIBUTIONS, CATEGORY_DOMAINS, DOMAINS
from tracker.sources import SOURCES
from tracker.views import coordinate_coverage


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


def share_places(count: int):
    """Move `count` shown, located EODI events and the dismissed ones to one place."""
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
    assert kpis["Countries"] == str(len(codes))
    assert kpis["Officially attributed"] == str(
        (shown["attribution"] == "official").sum()
    )
    assert len(at.get("plotly_chart")) == 3


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
    for column in ["attribution_label", "attributed_by", "end_date"]:
        assert column in table.columns
    assert "Polish government" in set(table["attributed_by"])
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
    assert "Countries view" in at.info[0].value
    assert len(at.get("plotly_chart")) == 2


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
    assert chart(at, 2).data[0].y == ("Germany",)
    at.segmented_control[0].set_value("Countries").run()
    assert chart(at, 1).data[0].locations == ("DEU",)


def test_multi_country_events_count_for_each_selected_country(sample_data_dir):
    at = run_app()
    at.multiselect(key="countries").set_value(["AT", "DE"]).run()
    assert not at.exception
    assert metrics(at)["Countries"] == "2"
    bars = chart(at, 2).data[0]
    assert dict(zip(bars.y, counts(bars))) == {"Germany": 4, "Austria": 1}


@pytest.mark.parametrize(
    "copies, note",
    [
        (1, "1 event has no country (open sea) and is on no map."),
        (3, "3 events have no country (open sea) and are on no map."),
    ],
)
def test_events_on_no_map_are_counted(sample_data_dir, copies, note):
    open_sea_rows(copies)
    at = run_app()
    assert note in captions(at)
    at.segmented_control[0].set_value("Countries").run()
    assert note in captions(at)
    at.multiselect(key="sources").set_value(["leiden"]).run()
    assert not at.exception
    at.segmented_control[0].set_value("Incidents").run()
    assert note in at.info[0].value


def test_one_located_event_gets_a_singular_caption(sample_data_dir):
    eodi = read_events("eodi")
    eodi[eodi["lat"].notna()].head(1).to_csv(events_path("eodi"), index=False)
    at = run_app()
    at.multiselect(key="sources").set_value(["eodi"]).run()
    assert "1 of 1 selected events has coordinates." in captions(at)


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
    assert "3 of them share their place with other events" in captions(at)
    at.multiselect(key="statuses").set_value(
        ["confirmed", "unconfirmed", "dismissed"]
    ).run()
    assert "4 of them share their place with other events" in captions(at)
    at.multiselect(key="categories").set_value(["airspace_incursion"]).run()
    assert "selected events have coordinates" in captions(at)
    assert "share their place" not in captions(at)


def test_status_filter_says_dismissed_events_are_hidden(sample_data_dir):
    at = run_app()
    status = at.multiselect(key="statuses")
    assert status.label == "Event status"
    assert "dismissed" in status.help.lower()
    assert "dismissed" not in status.value


def test_country_bar_keeps_the_countries_tied_at_the_cut(sample_data_dir):
    events = read_all_events()
    template = read_events("eodi").iloc[0]
    codes = [f"{a}{b}" for a in "ABC" for b in "DEFGHI"][:17]
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
    bars = chart(at, 2).data[0]
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
    assert f"{countries_only} events have a country and no coordinates" in text
    assert "in the Countries view only" in text
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
    assert f"{countries_only} events have a country and no coordinates" in info
    assert "Countries view" in info


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
    assert f"{countries_only} events have a country and no coordinates" in text
    at.multiselect(key="sources").set_value(["eodi", "leiden"]).run()
    at.multiselect(key="domains").set_value(["sabotage"]).run()
    assert not at.exception
    info = at.info[0].value
    sabotage = shown[shown["domain"] == "sabotage"]
    assert set(sabotage["source"]) == {"leiden"}
    assert f"Sources with no coordinates: {LEIDEN_TITLE}." in info
    assert EODI_TITLE not in info
    assert where_counts(sabotage)[1] == 1
    assert "1 event has a country and no coordinates" in info


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


def test_where_text_gives_the_events_on_no_map_like_the_helper(sample_data_dir):
    open_sea_rows(2)
    at = run_app()
    shown = expected_events(read_all_events())
    coverage = coordinate_coverage(shown)
    assert coverage.on_no_map == 2
    note = f"{coverage.on_no_map} events have no country (open sea) and are on no map."
    assert note in captions(at)
