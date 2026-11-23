"""The dashboard run headless with AppTest, on the stored data and on samples."""

import pytest
import streamlit as st
from conftest import APP
from streamlit.testing.v1 import AppTest

from tracker.schema import CATEGORIES
from tracker.sources import SOURCES, read_all_events


@pytest.fixture(autouse=True)
def clear_streamlit_cache():
    """The app caches the loaded events: every run must read the files again."""
    st.cache_data.clear()


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


def test_runs_on_the_stored_data():
    at = run_app()
    shown = expected_events(read_all_events())
    assert metrics(at)["Events"] == f"{len(shown):,}"


def test_counts_on_the_samples(sample_data_dir):
    at = run_app()
    shown = expected_events(read_all_events())
    codes = {code for cell in shown["countries"].dropna() for code in cell.split(";")}
    kpis = metrics(at)
    assert kpis["Events"] == str(len(shown))
    assert kpis["Countries"] == str(len(codes))
    assert kpis["Officially attributed to Russia"] == str(
        (shown["russia_link"] == "official").sum()
    )
    assert len(at.get("plotly_chart")) == 3


def test_every_category_has_a_filter_option(sample_data_dir):
    at = run_app()
    categories = at.sidebar.multiselect[1]
    assert categories.label == "Categories"
    assert len(categories.options) == len(CATEGORIES)


def test_credits_every_source(sample_data_dir):
    at = run_app()
    for source in SOURCES.values():
        assert source.citation in at.caption[-1].value
    table = at.dataframe[-1].value
    assert list(table["Source"]) == [source.title for source in SOURCES.values()]
    assert list(table["Licence"]) == [source.licence for source in SOURCES.values()]


def test_empty_selection_stops_with_a_message(sample_data_dir):
    at = run_app()
    at.sidebar.multiselect[0].set_value([]).run()
    assert not at.exception
    assert at.info[0].value == "No event matches these filters."
    assert not at.metric


def test_source_without_coordinates_keeps_the_other_charts(sample_data_dir):
    at = run_app()
    at.sidebar.multiselect[0].set_value(["leiden"]).run()
    assert not at.exception
    assert "Countries view" in at.info[0].value
    assert len(at.get("plotly_chart")) == 2


def test_countries_view(sample_data_dir):
    at = run_app()
    at.segmented_control[0].set_value("Countries").run()
    assert not at.exception
    assert not at.info
    assert len(at.get("plotly_chart")) == 3
