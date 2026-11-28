"""The quality row and the stale alert of the Stanford context source."""

import dataclasses
import json
from datetime import date, timedelta

import pandas as pd
import pytest
from test_alerts import FakeGh
from test_stanford import FIXTURE, changed, fetcher, seed

from tracker import alerts, cli, clock, quality, stanford
from tracker.errors import UserError
from tracker.sources import SOURCES, no_date
from tracker.store import LocalBackend, Store

pytestmark = pytest.mark.disable_socket

START = pd.Timestamp("2026-11-24 04:17:00", tz="UTC")
LAST = date(2026, 11, 22)
DAYS = [LAST - timedelta(days=n) for n in range(9, -1, -1)]
RUN = "https://example.org/run/1"


def set_clock(monkeypatch, days_later: int) -> date:
    now = START + pd.Timedelta(days=days_later)
    monkeypatch.setattr(clock, "utc_now", lambda: now)
    return now.date()


@pytest.fixture(autouse=True)
def stanford_source(monkeypatch):
    monkeypatch.setitem(
        SOURCES,
        stanford.SOURCE,
        dataclasses.replace(
            SOURCES["eodi"], table="context", updated_by_source=no_date
        ),
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


def run_day(
    store,
    monkeypatch,
    days_later: int,
    served: list[date] | dict[date, bytes],
    refused: bool = False,
) -> list[dict]:
    """One scheduled run: catch up, write the month files, run `tracker quality`.

    Args:
        served: The days that the server has, with the file of each (the
            fixture for a list).
        refused: The mapping refuses a file of this run, as `ingest` sees it.
    """
    today = set_clock(monkeypatch, days_later)
    files = served if isinstance(served, dict) else {day: FIXTURE for day in served}
    lines = stanford.catch_up(store, today, fetcher(files))
    months = stanford.fetched_months(lines)
    if refused:
        with pytest.raises(UserError, match="totalAircraftCount is 0"):
            stanford.write_months(store, months)
    else:
        stanford.write_months(store, months)
    cli.main(["quality", "--out", str(store.backend.root.parent / "out")])
    return lines


def quality_rows(store) -> pd.DataFrame:
    table = quality.read_table(store, quality.QUALITY_PATH)
    return table[table["source"] == stanford.SOURCE].reset_index(drop=True)


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
    assert (
        quality.causes(stanford.SOURCE, pd.DataFrame({"day": [1]}), fetched, None, now)
        == []
    )
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
    lines = run_day(store, monkeypatch, 7, DAYS)
    assert len(lines) == stanford.RECHECK_DAYS
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


def bad(day_file: bytes) -> bytes:
    return day_file.replace(b'"totalAircraftCount":35', b'"totalAircraftCount":0')


def test_a_download_that_the_mapping_refuses_moves_last_fetch_and_not_downloaded(
    store, monkeypatch
):
    run_day(store, monkeypatch, 1, DAYS)
    month = store.backend.root / stanford.month_path(stanford.month_of(LAST))
    before = month.read_bytes()
    files = {day: FIXTURE for day in DAYS} | {LAST: bad(FIXTURE)}
    run_day(store, monkeypatch, 2, files, refused=True)
    rows = quality_rows(store)

    one, two = (START + pd.Timedelta(days=n) for n in (1, 2))
    assert list(rows["downloaded_at"]) == [one.isoformat()] * 2
    assert list(rows["last_fetch_at"]) == [one.isoformat(), two.isoformat()]
    assert month.read_bytes() == before


def test_an_accepted_download_of_the_same_files_moves_downloaded(store, monkeypatch):
    run_day(store, monkeypatch, 1, DAYS)
    month = store.backend.root / stanford.month_path(stanford.month_of(LAST))
    before = month.read_bytes()
    run_day(store, monkeypatch, 2, DAYS)
    rows = quality_rows(store)

    assert list(rows["downloaded_at"]) == [
        (START + pd.Timedelta(days=n)).isoformat() for n in (1, 2)
    ]
    assert month.read_bytes() == before


def test_a_changed_file_of_a_day_already_held_does_not_reset_the_seven_day_clock(
    store, monkeypatch
):
    run_day(store, monkeypatch, 8, {day: FIXTURE for day in DAYS} | {LAST: changed(4)})

    assert stanford.new_day_stored_at(store) == START
    assert len(read_alerts(store)[stanford.SOURCE]) == 1


def test_a_run_where_every_day_answers_404_is_not_a_fetch_and_the_server_down_gives_both_causes(
    store, monkeypatch
):
    lines = run_day(store, monkeypatch, 4, [])
    assert lines == []
    assert quality.last_fetch(store, stanford.SOURCE) == START
    (found,) = read_alerts(store)[stanford.SOURCE]
    assert found.startswith("no successful fetch since 2026-11-24T04:17:00+00:00")

    run_day(store, monkeypatch, 8, [])
    first, second = read_alerts(store)[stanford.SOURCE]
    assert first.startswith("no successful fetch since")
    assert second.startswith("no new day stored since")
    row = quality_rows(store).iloc[-1]
    assert row["cause"] == f"{first}; {second}"


def test_the_context_row_has_no_event_columns_and_adds_no_event_ids(store, monkeypatch):
    run_day(store, monkeypatch, 1, DAYS)
    row = quality_rows(store).iloc[0]

    assert (row[quality.COUNT_COLUMNS[1:]] == 0).all()
    assert row[quality.EMPTY_COLUMNS].isna().all()
    ids = quality.read_table(store, quality.EVENT_IDS_PATH)
    assert stanford.SOURCE not in set(ids["source"])
