"""The Stanford history run: the days before the first stored day."""

import functools
import http.client
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


class CountingBackend(LocalBackend):
    """A local backend that records the paths of each commit."""

    def __init__(self, root):
        super().__init__(root)
        self.commits = []

    def commit(self, files, message):
        self.commits.append(sorted(files))
        super().commit(files, message)


def counting_store(tmp_path, monkeypatch):
    monkeypatch.setattr(
        clock, "utc_now", lambda: pd.Timestamp("2026-11-24 04:17:00", tz="UTC")
    )
    counting = Store(CountingBackend(tmp_path))
    seed(counting, {FIRST_STORED: FIXTURE})
    counting.backend.commits.clear()
    return counting


def payload_files(commit):
    return [path for path in commit if path.endswith(".json.br")]


@pytest.mark.disable_socket
def test_200_days_make_4_commits_with_month_files_beside_the_next_payloads(
    tmp_path, monkeypatch
):
    counting = counting_store(tmp_path, monkeypatch)

    result = stanford.history(counting, fetcher(YEAR))

    commits = counting.backend.commits
    months = {stanford.month_of(day) for day in stanford.stored_days(counting)}
    assert result.stored == 200
    assert [len(payload_files(commit)) for commit in commits] == [90, 90, 20, 0]
    assert all(len(commit) <= 99 for commit in commits)
    assert not any(stanford.month_path(month) in commits[0] for month in months)
    assert any(path.startswith("clean/stanford/") for path in commits[1])
    assert payload_files(commits[1])
    written = {path for commit in commits for path in commit}
    assert {stanford.month_path(month) for month in months} <= written


@pytest.mark.disable_socket
def test_the_history_commit_size_leaves_room_for_the_month_files_of_a_chunk():
    chunk = stanford.HISTORY_PAYLOADS_PER_SAVE
    first = date(2022, 1, 1)
    worst = max(
        len({stanford.month_of(first + timedelta(days=n + k)) for k in range(chunk)})
        for n in range(1500)
    )
    assert worst + 1 + 1 + 1 <= stanford.MAX_FILES_PER_COMMIT - chunk


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
    assert len(kept) == 1 + 90
    assert kept[:-1] == [
        FIRST_STORED - timedelta(days=90) + timedelta(days=n) for n in range(90)
    ]
    asked = []

    def healthy(days):
        asked.extend(days)
        return fetcher(YEAR)(days)

    stanford.history(local, healthy)

    assert asked == [kept[0] - timedelta(days=n) for n in range(1, 111)]
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

    assert first.stored == 2 * 90
    assert second.stored == 200 - 2 * 90


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


class FakeConnection:
    """A connection whose request, answer or body fails as `fail` says."""

    fail = "timeout"

    def __init__(self, *args, **kwargs):
        pass

    def request(self, *args, **kwargs):
        if self.fail == "timeout":
            raise TimeoutError("timed out")

    def getresponse(self):
        return self

    def read(self):
        if self.fail == "cut":
            raise http.client.IncompleteRead(b"abc", 10)
        return b"not brotli"

    status = 200

    def getheader(self, *args):
        return "br"

    def close(self):
        pass


@pytest.mark.disable_socket
@pytest.mark.parametrize(
    ("fail", "text"),
    [("timeout", "TimeoutError"), ("cut", "IncompleteRead"), ("body", "error")],
)
def test_a_timeout_a_cut_answer_or_a_bad_body_stops_the_run_with_the_day(
    local, monkeypatch, fail, text
):
    monkeypatch.setattr(FakeConnection, "fail", fail)
    monkeypatch.setattr(stanford.http.client, "HTTPSConnection", FakeConnection)
    monkeypatch.setattr(stanford, "tls_context", lambda: None)
    day = FIRST_STORED - timedelta(days=1)

    with pytest.raises(UserError, match=f"{day}: .*{text}"):
        stanford.history(local)

    assert stanford.stored_days(local) == [FIRST_STORED]


@pytest.mark.disable_socket
def test_a_stopped_run_has_the_month_files_of_its_stored_days_in_one_last_commit(
    tmp_path, monkeypatch
):
    counting = counting_store(tmp_path, monkeypatch)
    healthy = fetcher(YEAR)
    asked = []

    def stops_on_the_third_chunk(days):
        asked.append(list(days))
        if len(asked) == 3:
            raise OSError(f"{days[0]}: https://x: timed out")
        return healthy(days)

    with pytest.raises(UserError, match="timed out"):
        stanford.history(counting, stops_on_the_third_chunk)

    kept = stanford.stored_days(counting)
    months = sorted({stanford.month_of(day) for day in kept})
    commits = counting.backend.commits
    assert len(kept) == 1 + 2 * 90
    assert all(
        counting.backend.read(stanford.month_path(month)) is not None
        for month in months
    )
    assert [len(payload_files(commit)) for commit in commits] == [90, 90, 0]
    assert stanford.month_path(stanford.month_of(asked[1][-1])) in commits[-1]


@pytest.mark.disable_socket
def test_a_rerun_writes_the_month_files_that_a_killed_run_left_out(
    tmp_path, monkeypatch
):
    counting = counting_store(tmp_path, monkeypatch)
    healthy = fetcher(YEAR)
    asked = []

    def killed_on_the_second_chunk(days):
        asked.append(list(days))
        if len(asked) == 2:
            raise KeyboardInterrupt
        return healthy(days)

    with pytest.raises(KeyboardInterrupt):
        stanford.history(counting, killed_on_the_second_chunk)

    left_out = sorted(stanford.months_without_file(counting))
    assert len(left_out) >= 3
    counting.backend.commits.clear()

    stanford.history(counting, healthy)

    first = counting.backend.commits[0]
    open_month = stanford.month_of(asked[0][-1])
    closed = [month for month in left_out if month != open_month]
    assert closed
    assert all(stanford.month_path(month) in first for month in closed)
    assert payload_files(first)
    assert len(first) <= 99
    assert stanford.months_without_file(counting) == set()
