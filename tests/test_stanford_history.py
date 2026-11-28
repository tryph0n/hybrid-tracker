"""The Stanford history run: the days before the first stored day."""

import functools
import json
from datetime import date, timedelta

import brotli
import pandas as pd
import pytest
import test_stanford
from test_stanford import FIXTURE, fetcher, heatmap_path, seed, served
from test_stanford_quality import read_alerts, set_clock
from test_stanford_quality import store as quality_store

from tracker import cli, clock, quality, stanford
from tracker.errors import UserError
from tracker.store import LocalBackend, Store

tls_server = test_stanford.tls_server
store = quality_store

FIRST_STORED = date(2022, 7, 20)
YEAR_START = date(2022, 1, 1)


@pytest.fixture
def local(tmp_path, monkeypatch):
    now = pd.Timestamp("2026-11-24 04:17:00", tz="UTC")
    monkeypatch.setattr(clock, "utc_now", lambda: now)
    held = Store(LocalBackend(tmp_path))
    seed(held, {FIRST_STORED: FIXTURE})
    return held


def every_day(first: date, count: int) -> dict[date, bytes]:
    return {first + timedelta(days=n): FIXTURE for n in range(count)}


YEAR = every_day(YEAR_START, 200)


@pytest.mark.disable_socket
def test_a_404_day_is_listed_and_the_run_stores_the_days_around_it(local):
    missing = date(2022, 3, 1)
    days = {day: FIXTURE for day in YEAR if day != missing}

    result = stanford.history(local, fetcher(days))

    assert result.missing == [missing]
    assert result.stored == 199
    assert missing not in stanford.stored_days(local)
    assert YEAR_START in stanford.stored_days(local)
    assert str(missing) in stanford.history_summary(result)


@pytest.mark.disable_socket
def test_each_commit_has_at_most_99_files_and_200_days_make_3_payload_commits(
    tmp_path, monkeypatch
):
    commits = []

    class CountingBackend(LocalBackend):
        def commit(self, files, message):
            commits.append([path for path in files if path.endswith(".json.br")])
            commits[-1].append(len(files))
            super().commit(files, message)

    monkeypatch.setattr(
        clock, "utc_now", lambda: pd.Timestamp("2026-11-24 04:17:00", tz="UTC")
    )
    counting = Store(CountingBackend(tmp_path))
    seed(counting, {FIRST_STORED: FIXTURE})
    commits.clear()

    result = stanford.history(counting, fetcher(YEAR))

    assert result.stored == 200
    adding = [commit for commit in commits if len(commit) > 1]
    assert [len(commit) - 1 for commit in adding] == [97, 97, 6]
    assert all(commit[-1] <= 99 for commit in commits)


@pytest.mark.disable_socket
def test_a_server_error_stops_the_run_with_the_day_and_a_rerun_fetches_only_the_rest(
    local,
):
    calls = []

    def fails_on_the_second_chunk(days):
        calls.append(list(days))
        if len(calls) == 2:
            raise OSError(f"{days[0]}: https://x: HTTP 500")
        return fetcher(YEAR)(days)

    with pytest.raises(UserError, match="HTTP 500"):
        stanford.history(local, fails_on_the_second_chunk)

    kept = stanford.stored_days(local)
    assert len(kept) == 1 + 97
    assert kept[:-1] == [
        FIRST_STORED - timedelta(days=97) + timedelta(days=n) for n in range(97)
    ]
    asked = []

    def healthy(days):
        asked.extend(days)
        return fetcher(YEAR)(days)

    stanford.history(local, healthy)

    assert asked == [kept[0] - timedelta(days=n) for n in range(1, 104)]
    assert stanford.stored_days(local)[0] == YEAR_START
    assert len(stanford.stored_days(local)) == 201


@pytest.mark.disable_socket
def test_a_run_stops_at_the_commit_cap_and_a_rerun_goes_on(tmp_path, monkeypatch):
    monkeypatch.setattr(stanford, "MAX_HISTORY_COMMITS", 2)
    monkeypatch.setattr(
        clock, "utc_now", lambda: pd.Timestamp("2026-11-24 04:17:00", tz="UTC")
    )
    held = Store(LocalBackend(tmp_path))
    seed(held, {FIRST_STORED: FIXTURE})

    first = stanford.history(held, fetcher(YEAR))
    second = stanford.history(held, fetcher(YEAR))

    assert first.stored == 2 * 97
    assert second.stored == 200 - 2 * 97


@pytest.mark.disable_socket
def test_a_history_run_leaves_the_stale_cause_as_it_was(store, monkeypatch):
    before = stanford.new_day_stored_at(store)
    later = set_clock(monkeypatch, 8)

    result = stanford.history(store, fetcher(every_day(YEAR_START, 3)))
    cli.main(["quality", "--out", str(store.backend.root.parent / "out")])

    assert result.stored == 3
    assert stanford.new_day_stored_at(store) == before
    found = read_alerts(store)[stanford.SOURCE]
    assert any(text.startswith("no new day stored since") for text in found)
    assert later - before.date() > timedelta(days=7)


@pytest.mark.disable_socket
def test_a_day_newer_than_all_stored_days_still_counts_as_new(local, monkeypatch):
    later = pd.Timestamp("2026-11-30 04:17:00", tz="UTC")
    monkeypatch.setattr(clock, "utc_now", lambda: later)
    local.save(
        stanford.make_payload(
            date(2026, 11, 29), "u", 200, brotli.compress(FIXTURE), "br"
        )
    )

    assert stanford.new_day_stored_at(local) == later


@pytest.mark.enable_socket
def test_the_run_asks_for_brotli_and_stores_each_day_compressed(
    tls_server, local, monkeypatch
):
    port, cert, seen, _, answer = tls_server
    days = every_day(FIRST_STORED - timedelta(days=2), 2)
    answer["files"] = served(days)
    monkeypatch.setattr(
        stanford,
        "fetch_days",
        functools.partial(
            stanford.fetch_days,
            context=stanford.tls_context(cert),
            host="localhost",
            port=port,
        ),
    )
    monkeypatch.setattr(stanford, "FIRST_DAY", FIRST_STORED - timedelta(days=2))

    stanford.history(local)

    assert seen == ["br", "br"]
    for day in days:
        kept = local.latest(stanford.SOURCE, stanford.payload_name(day))
        assert kept is not None
        assert kept != FIXTURE
        assert brotli.decompress(kept) == FIXTURE


@pytest.mark.enable_socket
def test_a_http_500_names_the_day_and_stops_the_run(tls_server, local, monkeypatch):
    port, cert, _, _, answer = tls_server
    answer["status"] = 500
    monkeypatch.setattr(
        stanford,
        "fetch_days",
        functools.partial(
            stanford.fetch_days,
            context=stanford.tls_context(cert),
            host="localhost",
            port=port,
        ),
    )
    day = FIRST_STORED - timedelta(days=1)

    with pytest.raises(UserError, match=f"{day}: .*HTTP 500"):
        stanford.history(local)

    assert stanford.stored_days(local) == [FIRST_STORED]
    assert heatmap_path(day) in answer["paths"]


@pytest.mark.disable_socket
def test_the_command_writes_the_run_summary(local, tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKER_STORE", "local")
    monkeypatch.setenv("TRACKER_STORE_DIR", str(tmp_path))
    summary = tmp_path.parent / f"{tmp_path.name}-summary.md"
    monkeypatch.setenv(quality.SUMMARY_VAR, str(summary))
    days = {day: FIXTURE for day in YEAR if day != date(2022, 3, 1)}
    monkeypatch.setattr(
        stanford, "fetch_days", lambda requested, **_: fetcher(days)(requested)
    )

    cli.main(["history"])

    text = summary.read_text()
    assert "Days stored: 199" in text
    assert "Missing days (404): 1 (2022-03-01)" in text
    assert "Time: " in text
    assert "Added to the raw store: " in text
    summary.unlink()
    assert json.loads((tmp_path / "clean/stanford.json").read_text())["payloads"]
