"""The quality row and the stale alert of the Stanford context source."""

import dataclasses
import json
from datetime import date, timedelta

import pandas as pd
import pytest
from test_alerts import FakeGh
from test_stanford import FIXTURE, fetcher, seed

from tracker import alerts, cli, clock, quality, stanford
from tracker.sources import SOURCES
from tracker.store import LocalBackend, Store

pytestmark = pytest.mark.disable_socket

START = pd.Timestamp("2026-11-24 04:17:00", tz="UTC")
LAST = date(2026, 11, 22)
DAYS = [LAST - timedelta(days=n) for n in (3, 2, 1, 0)]
RUN = "https://example.org/run/1"


def set_clock(monkeypatch, days_later: int) -> date:
    now = START + pd.Timedelta(days=days_later)
    monkeypatch.setattr(clock, "utc_now", lambda: now)
    return now.date()


@pytest.fixture(autouse=True)
def stanford_source(monkeypatch):
    monkeypatch.setitem(
        SOURCES, stanford.SOURCE, dataclasses.replace(SOURCES["eodi"], table="context")
    )


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKER_STORE", "local")
    monkeypatch.setenv("TRACKER_STORE_DIR", str(tmp_path / "store"))
    monkeypatch.delenv(quality.SUMMARY_VAR, raising=False)
    set_clock(monkeypatch, 0)
    store = Store(LocalBackend(tmp_path / "store"))
    seed(store, {day: FIXTURE for day in DAYS})
    stanford.write_months(store, {stanford.month_of(LAST)})
    return store


def run_day(store, monkeypatch, days_later: int, served: list[date]) -> list[dict]:
    """One scheduled run: catch up, write the month files, run `tracker quality`."""
    today = set_clock(monkeypatch, days_later)
    fetch = fetcher({day: FIXTURE for day in served})
    lines = stanford.catch_up(store, today, fetch)
    stanford.write_months(store, stanford.fetched_months(lines))
    cli.main(["quality", "--out", str(store.backend.root.parent / "out")])
    return lines


def alert_actions(store, gh) -> list[str]:
    path = store.backend.root.parent / "out" / quality.ALERTS_NAME
    done = alerts.sync(path, RUN, gh)
    return [action.kind for action in done if action.source == stanford.SOURCE]


def read_alerts(store) -> dict[str, list[str]]:
    path = store.backend.root.parent / "out" / quality.ALERTS_NAME
    return json.loads(path.read_text(encoding="utf-8"))


def test_a_404_for_a_day_not_published_is_a_successful_fetch(store, monkeypatch):
    lines = run_day(store, monkeypatch, 1, DAYS)

    assert {line["status"] for line in lines} == {200}
    fetched = quality.last_fetch(store, stanford.SOURCE)
    assert fetched == START + pd.Timedelta(days=1)
    now = clock.utc_now()
    assert quality.causes(stanford.SOURCE, pd.Series([1]), fetched, None, now) == []
    assert stanford.SOURCE not in read_alerts(store)


def test_the_quality_row_gives_the_newest_stored_day_and_a_blank_update_date(
    store, monkeypatch
):
    run_day(store, monkeypatch, 1, DAYS)
    row = quality.read_table(store, quality.QUALITY_PATH).set_index("source")
    row = row.loc[stanford.SOURCE]

    rows_per_day = len(stanford.reduce_day(LAST, FIXTURE))
    assert row["rows"] == rows_per_day * len(DAYS)
    assert row["latest_event_date"] == "2026-11-22"
    assert pd.isna(row["updated_by_source"])
    assert row["downloaded_at"] == (START + pd.Timedelta(days=1)).isoformat()
    assert row["last_fetch_at"] == row["downloaded_at"]
    assert pd.isna(row["cause"])
    assert row[quality.EMPTY_COLUMNS].isna().all()
    assert stanford.SOURCE not in read_alerts(store)


def test_a_run_that_fetches_the_same_files_moves_downloaded_and_not_the_newest_day(
    store, monkeypatch
):
    run_day(store, monkeypatch, 1, DAYS)
    run_day(store, monkeypatch, 2, DAYS)
    table = quality.read_table(store, quality.QUALITY_PATH)
    rows = table[table["source"] == stanford.SOURCE]

    assert list(rows["downloaded_at"]) == [
        (START + pd.Timedelta(days=n)).isoformat() for n in (1, 2)
    ]
    assert list(rows["latest_event_date"]) == ["2026-11-22"] * 2


def test_the_alert_opens_after_seven_days_with_no_new_day_and_closes_on_a_new_one(
    store, monkeypatch
):
    gh = FakeGh()
    run_day(store, monkeypatch, 7, DAYS)
    assert stanford.SOURCE not in read_alerts(store)
    assert alert_actions(store, gh) == []

    run_day(store, monkeypatch, 8, DAYS)
    found = read_alerts(store)[stanford.SOURCE]
    assert len(found) == 1
    assert found[0].startswith("no new day stored since 2026-11-24T04:17:00+00:00")
    assert alert_actions(store, gh) == ["open"]

    run_day(store, monkeypatch, 9, DAYS)
    assert stanford.SOURCE in read_alerts(store)
    assert alert_actions(store, gh) == ["comment"]
    assert "Source alert: stanford" in gh.open_titles()

    run_day(store, monkeypatch, 10, [*DAYS, LAST + timedelta(days=1)])
    assert stanford.SOURCE not in read_alerts(store)
    assert alert_actions(store, gh) == ["close"]
    assert "Source alert: stanford" not in gh.open_titles()
