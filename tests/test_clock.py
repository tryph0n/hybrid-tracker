"""The period filter cuts at the UTC day, whatever the time zone of the process."""

import time

import pandas as pd
import pytest
import streamlit as st
from conftest import read_all_events
from test_app import expected_events, metrics, run_app

from tracker import clock


@pytest.fixture
def far_from_utc(monkeypatch, sample_data_dir, hub_fetches):
    """Process in UTC+14. Clock set 30 minutes before midnight UTC, 29 days after the latest event.

    The local day is already the next one, so a local "today" would put the latest event
    exactly on the 30-day boundary and drop it.
    """
    latest = pd.Timestamp(read_all_events()["date"].max())
    st.cache_data.clear()
    monkeypatch.setenv("TZ", "Pacific/Kiritimati")
    time.tzset()
    now = (latest + pd.Timedelta(days=29, hours=23, minutes=30)).tz_localize("UTC")
    monkeypatch.setattr(clock, "utc_now", lambda: now)
    yield latest
    monkeypatch.undo()
    time.tzset()


def test_today_is_the_utc_day(far_from_utc):
    assert clock.utc_today() == far_from_utc + pd.Timedelta(days=29)


def test_period_filter_and_last_30_days_share_the_utc_day(
    far_from_utc,
):
    shown = expected_events(read_all_events())
    dates = pd.to_datetime(shown["date"])
    cut = clock.utc_today() - pd.Timedelta(days=30)
    expected = int((dates > cut).sum())
    assert expected > 0
    at = run_app()
    assert metrics(at)["Last 30 days"] == str(expected)
    at.selectbox(key="period").set_value("Last 30 days").run()
    assert not at.exception
    assert metrics(at)["Events"] == str(expected)
