import dataclasses
import io
import json

import pandas as pd
import pytest
from conftest import EVENT_SOURCES, FIXTURES, recorded_payloads
from test_stanford import DAY, FIXTURE, seed

from tracker import cli, clock, ingest, quality, sources, stanford
from tracker.schema import read_csv
from tracker.store import LocalBackend, Payload, Store

pytestmark = pytest.mark.disable_socket

START = "2026-03-05 10:00:00"


def set_clock(monkeypatch, stamp: str) -> None:
    now = pd.Timestamp(stamp, tz="UTC")
    monkeypatch.setattr(clock, "utc_now", lambda: now)


def days_after(days: float) -> str:
    return str(pd.Timestamp(START) + pd.Timedelta(days=days))


def put_clean(store: Store, name: str, events: pd.DataFrame) -> None:
    table = io.BytesIO()
    events.to_parquet(table, index=False)
    store.backend.commit({ingest.clean_path(name): table.getvalue()}, "test")


def fetch(store: Store, name: str, status: int = 200) -> None:
    store.save(Payload(name, "file.csv", b"a,b\n1,2\n", "https://example.org", status))


def fixture_events(name: str) -> pd.DataFrame:
    return read_csv(FIXTURES / "events" / f"{name}.csv")


def put_context(store: Store) -> None:
    """Store a day of the context source and its month file, as a run does."""
    seed(store, {DAY: FIXTURE})
    stanford.write_months(store, {stanford.month_of(DAY)})


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch, tmp_path):
    set_clock(monkeypatch, START)
    monkeypatch.delenv(quality.SUMMARY_VAR, raising=False)


@pytest.fixture
def store(tmp_path) -> Store:
    store = Store(LocalBackend(tmp_path / "store"))
    for name in EVENT_SOURCES:
        put_clean(store, name, fixture_events(name))
        fetch(store, name)
    put_context(store)
    return store


@pytest.fixture
def out(tmp_path):
    return tmp_path / "out"


def master(store: Store) -> pd.DataFrame:
    table = quality.read_table(store, quality.QUALITY_PATH)
    assert table is not None
    return table


def test_two_runs_give_one_row_per_source_for_each_run(store, out, monkeypatch):
    quality.run(store, out)
    set_clock(monkeypatch, days_after(1))
    quality.run(store, out)

    for table, expected in [
        (master(store), sources.SOURCES),
        (pd.read_parquet(out / "nc" / "quality.parquet"), sources.SOURCES),
        (pd.read_parquet(out / "open" / "quality.parquet"), EVENT_SOURCES),
    ]:
        counts = table.groupby("run_at")["source"].apply(sorted)
        assert len(counts) == 2
        assert all(names == sorted(expected) for names in counts)
    assert list(master(store).columns) == quality.COLUMNS


def test_first_run_adds_every_row_and_removes_none(store, out):
    quality.run(store, out)

    for row in master(store).itertuples():
        if row.source == stanford.SOURCE:
            continue
        assert row.rows == len(fixture_events(row.source))
        assert row.rows_added == row.rows
        assert row.rows_removed == 0


def test_unchanged_clean_table_adds_and_removes_nothing(store, out, monkeypatch):
    quality.run(store, out)
    set_clock(monkeypatch, days_after(1))
    second = quality.run(store, out)

    assert (second["rows_added"] == 0).all()
    assert (second["rows_removed"] == 0).all()


def test_rows_added_and_removed_count_event_ids(store, out, monkeypatch):
    quality.run(store, out)
    events = fixture_events("eodi")
    added = events.head(1).copy()
    added["event_id"] = "eodi:NEW"
    put_clean(store, "eodi", pd.concat([events.iloc[2:], added], ignore_index=True))
    set_clock(monkeypatch, days_after(1))
    second = quality.run(store, out).set_index("source")

    assert second.loc["eodi", "rows"] == len(events) - 1
    assert second.loc["eodi", "rows_added"] == 1
    assert second.loc["eodi", "rows_removed"] == 2
    assert second.loc["leiden", "rows_added"] == 0


def test_trend_survives_a_missing_build_folder(store, out, tmp_path, monkeypatch):
    quality.run(store, out)
    set_clock(monkeypatch, days_after(1))
    quality.run(store, tmp_path / "other")

    assert master(store)["run_at"].nunique() == 2
    assert (
        pd.read_parquet(tmp_path / "other" / "open" / "quality.parquet")[
            "run_at"
        ].nunique()
        == 2
    )


def test_open_family_file_holds_only_open_sources(store, out, monkeypatch):
    nc = fixture_events("eodi").head(2).copy()
    nc["source"] = "fakenc"
    nc["event_id"] = ["fakenc:1", "fakenc:2"]
    source = dataclasses.replace(sources.SOURCES["eodi"], family="nc")
    monkeypatch.setitem(sources.SOURCES, "fakenc", source)
    put_clean(store, "fakenc", nc)
    fetch(store, "fakenc")
    quality.run(store, out)

    assert "fakenc" not in set(
        pd.read_parquet(out / "open" / "quality.parquet")["source"]
    )
    assert "fakenc" in set(pd.read_parquet(out / "nc" / "quality.parquet")["source"])


def test_none_attribution_with_empty_actors_does_not_raise_the_share(store, out):
    current = quality.run(store, out).set_index("source")

    eodi = fixture_events("eodi")
    assert (eodi["attribution"] == "none").sum() == 9
    assert eodi["actors"].isna().sum() == 9
    assert current.loc["eodi", "empty_actors"] == 0.0


def test_empty_actors_of_an_attributed_row_raises_the_share(store, out):
    events = fixture_events("eodi")
    events.loc[events["attribution"] == "official", "actors"] = None
    put_clean(store, "eodi", events)
    current = quality.run(store, out).set_index("source")

    assert current.loc["eodi", "empty_actors"] == 1.0


def test_empty_share_counts_coordinates(store, out):
    current = quality.run(store, out).set_index("source")

    assert current.loc["eodi", "empty_lat"] == pytest.approx(0.2)
    assert current.loc["leiden", "empty_lon"] == 1.0
    assert current.loc["eodi", "empty_event_id"] == 0.0


def test_latest_event_date(store, out):
    current = quality.run(store, out).set_index("source")

    assert (
        current.loc["eodi", "latest_event_date"] == fixture_events("eodi")["date"].max()
    )


def test_summary_gives_none_events_and_new_none_events(
    store, out, tmp_path, monkeypatch
):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv(quality.SUMMARY_VAR, str(summary))
    quality.run(store, out)
    first = summary.read_text()
    assert "| source |" in first
    assert "Events with attribution none: 9 (9 new in this run)." in first

    new = fixture_events("eodi").head(1).copy()
    new["event_id"] = "eodi:NEW"
    new["attribution"] = "none"
    put_clean(
        store, "eodi", pd.concat([fixture_events("eodi"), new], ignore_index=True)
    )
    set_clock(monkeypatch, days_after(1))
    quality.run(store, out)

    assert (
        "Events with attribution none: 10 (1 new in this run)."
        in (summary.read_text()[len(first) :])
    )


def test_summary_is_logged_without_a_summary_file(store, out, caplog):
    caplog.set_level("INFO")
    quality.run(store, out)

    assert "Events with attribution none: 9" in caplog.text


def read_alerts(out) -> dict[str, list[str]]:
    return json.loads((out / quality.ALERTS_NAME).read_text(encoding="utf-8"))


def test_a_run_with_no_problem_writes_empty_alerts_and_blank_causes(store, out):
    current = quality.run(store, out)

    assert read_alerts(out) == {}
    assert current["cause"].isna().all()


def test_live_source_with_no_row_is_reported_and_the_run_passes(store, out):
    empty = fixture_events("eodi").iloc[0:0]
    put_clean(store, "eodi", empty)

    current = quality.run(store, out).set_index("source")

    assert read_alerts(out) == {"eodi": ["no row in the clean table"]}
    assert current.loc["eodi", "cause"] == "no row in the clean table"
    assert (out / "open" / "quality.parquet").exists()
    assert master(store).set_index("source").loc["eodi", "rows"] == 0


def test_a_source_with_no_clean_table_keeps_a_row_with_its_cause(
    store, out, tmp_path, monkeypatch
):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv(quality.SUMMARY_VAR, str(summary))
    (tmp_path / "store" / ingest.clean_path("eodi")).unlink()

    current = quality.run(store, out).set_index("source")

    assert read_alerts(out) == {"eodi": ["no clean table"]}
    assert current.loc["eodi", "cause"] == "no clean table"
    assert current.loc["eodi", "rows"] == 0
    assert current.loc["leiden", "rows"] > 0
    assert pd.isna(current.loc["leiden", "cause"])
    published = pd.read_parquet(out / "open" / "quality.parquet")
    assert set(published["source"]) == set(EVENT_SOURCES)
    assert "`eodi`: no clean table" in summary.read_text()


def test_a_source_that_is_back_has_no_cause_at_the_next_run(
    store, out, tmp_path, monkeypatch
):
    (tmp_path / "store" / ingest.clean_path("eodi")).unlink()
    quality.run(store, out)

    set_clock(monkeypatch, days_after(1))
    put_clean(store, "eodi", fixture_events("eodi"))
    current = quality.run(store, out).set_index("source")

    assert read_alerts(out) == {}
    assert pd.isna(current.loc["eodi", "cause"])
    assert current.loc["eodi", "rows_added"] == len(fixture_events("eodi"))


def test_live_source_fetched_four_days_ago_is_reported_and_static_is_not(
    tmp_path, out, monkeypatch
):
    store = Store(LocalBackend(tmp_path / "store"))
    for name in EVENT_SOURCES:
        put_clean(store, name, fixture_events(name))
    fetch(store, "eodi")
    fetch(store, "leiden")
    put_context(store)
    set_clock(monkeypatch, days_after(4))
    current = quality.run(store, out)

    alerts = read_alerts(out)
    assert list(alerts) == ["eodi", "stanford"]
    assert alerts["eodi"][0].startswith("no successful fetch since")
    assert master(store)["run_at"].nunique() == 1
    assert len(current) == len(sources.SOURCES)
    assert (out / "open" / "quality.parquet").exists()

    fetch(store, "eodi")
    put_context(store)
    quality.run(store, out)
    assert read_alerts(out) == {}


def test_live_source_fetched_three_days_ago_passes(store, out, monkeypatch):
    set_clock(monkeypatch, days_after(3))
    quality.run(store, out)


def test_identical_payload_checked_again_counts_as_a_fetch(store, out, monkeypatch):
    set_clock(monkeypatch, days_after(2))
    (line,) = [store.save(Payload("eodi", "file.csv", b"a,b\n1,2\n", "u", 200))]
    assert line["stored"] is False
    set_clock(monkeypatch, days_after(4))
    current = quality.run(store, out).set_index("source")

    assert (
        current.loc["eodi", "last_fetch_at"]
        == pd.Timestamp(days_after(2), tz="UTC").isoformat()
    )


def test_live_source_without_a_successful_fetch_is_reported(tmp_path, out, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv(quality.SUMMARY_VAR, str(summary))
    store = Store(LocalBackend(tmp_path / "store"))
    for name in EVENT_SOURCES:
        put_clean(store, name, fixture_events(name))
    fetch(store, "eodi", status=503)
    put_context(store)

    quality.run(store, out)

    assert read_alerts(out) == {"eodi": ["no successful fetch"]}
    assert "`eodi`: no successful fetch" in summary.read_text()


def test_a_refresh_that_failed_after_a_download_is_reported_until_it_succeeds(
    store, out, monkeypatch
):
    ingest.write_failure("eodi", "ValueError: Unknown category 'balloon'", store)
    first = quality.run(store, out).set_index("source")

    assert read_alerts(out) == {
        "eodi": ["refresh failed: ValueError: Unknown category 'balloon'"]
    }
    assert first.loc["eodi", "cause"] == (
        "refresh failed: ValueError: Unknown category 'balloon'"
    )

    ingest.write_failure("eodi", None, store)
    set_clock(monkeypatch, days_after(1))
    second = quality.run(store, out).set_index("source")

    assert read_alerts(out) == {}
    assert pd.isna(second.loc["eodi", "cause"])


def test_the_causes_of_one_source_are_joined_in_its_row(store, out, tmp_path):
    (tmp_path / "store" / ingest.clean_path("eodi")).unlink()
    ingest.write_failure("eodi", "RuntimeError: HTTP 500", store)

    current = quality.run(store, out).set_index("source")

    assert (
        current.loc["eodi", "cause"]
        == "no clean table; refresh failed: RuntimeError: HTTP 500"
    )
    assert read_alerts(out)["eodi"] == [
        "no clean table",
        "refresh failed: RuntimeError: HTTP 500",
    ]


def test_the_run_fails_and_writes_nothing_when_no_source_has_a_clean_table(
    tmp_path, out
):
    store = Store(LocalBackend(tmp_path / "store"))

    with pytest.raises(ValueError, match="no source has a clean table"):
        quality.run(store, out)
    assert not out.exists()
    assert quality.read_table(store, quality.QUALITY_PATH) is None


def test_cli_runs_quality(store, out, tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKER_STORE", "local")
    monkeypatch.setenv("TRACKER_STORE_DIR", str(tmp_path / "store"))
    cli.main(["quality", "--out", str(out)])

    assert (out / "open" / "quality.parquet").exists()


T1, T2, T3 = (pd.Timestamp(days_after(d), tz="UTC").isoformat() for d in (0, 1, 2))


def serve_files(monkeypatch, name: str, files: dict[str, bytes], **changes) -> None:
    """Make source `name` download `files`, with the given fields replaced."""
    monkeypatch.setitem(
        sources.SOURCES,
        name,
        dataclasses.replace(
            sources.SOURCES[name],
            extract=lambda: [
                Payload(name, file, content, f"https://e/{file}", 200)
                for file, content in files.items()
            ],
            **changes,
        ),
    )


def ingest_at(monkeypatch, store: Store, name: str, day: int, files, **changes):
    set_clock(monkeypatch, days_after(day))
    if "all.bin" in files:
        changes.setdefault("normalize", fixture_normalize())
        changes.setdefault("updated_by_source", sources.no_date)
    serve_files(monkeypatch, name, files, **changes)
    ingest.ingest(name, store)


def quality_row(store: Store, out, name: str) -> pd.Series:
    return quality.run(store, out).set_index("source").loc[name]


def fixture_normalize():
    """A `normalize` that maps `good` and rejects the other contents."""
    events = fixture_events("eodi")
    invalid = events.copy()
    invalid["date_precision"] = "never"
    results = {b"good": events, b"short": events.head(1), b"invalid": invalid}

    def normalize(files):
        content = files["all.bin"]
        if content == b"malformed":
            raise ValueError("malformed file")
        return results[content]

    return normalize


@pytest.fixture
def ingested(tmp_path, monkeypatch) -> Store:
    """A store where each source was ingested from its recorded files at T1."""
    store = Store(LocalBackend(tmp_path / "ingested"))
    ingest_at(monkeypatch, store, "leiden", 0, leiden_files())
    ingest_at(monkeypatch, store, "eodi", 0, recorded_payloads("eodi"))
    return store


def leiden_files() -> dict[str, bytes]:
    files = recorded_payloads("leiden")
    files["version.json"] = (FIXTURES / "leiden_dataverse_version.json").read_bytes()
    return files


@pytest.mark.parametrize("rejected", [b"malformed", b"invalid", b"short"])
def test_a_rejected_download_does_not_move_downloaded_at(
    store, out, monkeypatch, rejected
):
    good = {"all.bin": b"good"}
    ingest_at(monkeypatch, store, "eodi", 0, good, normalize=fixture_normalize())
    with pytest.raises(ValueError):
        ingest_at(
            monkeypatch,
            store,
            "eodi",
            1,
            {"all.bin": rejected},
            normalize=fixture_normalize(),
        )
    set_clock(monkeypatch, days_after(2))
    row = quality_row(store, out, "eodi")

    assert row["downloaded_at"] == T1
    assert row["last_fetch_at"] == T2


def test_an_accepted_download_with_the_same_file_moves_downloaded_at(
    store, out, monkeypatch
):
    for day in (0, 2):
        ingest_at(
            monkeypatch,
            store,
            "eodi",
            day,
            {"all.bin": b"good"},
            normalize=fixture_normalize(),
        )
    row = quality_row(store, out, "eodi")

    assert row["downloaded_at"] == T3
    assert [line["stored"] for line in store.manifest("eodi")][-2:] == [True, False]


def test_a_rebuild_with_no_network_gives_the_same_two_dates(ingested, out, monkeypatch):
    before = quality.run(ingested, out).set_index("source")

    set_clock(monkeypatch, days_after(2))
    ingest.rebuild(None, ingested)
    after = quality.run(ingested, out).set_index("source")

    for name in ("eodi", "leiden"):
        assert before.loc[name, "downloaded_at"] == after.loc[name, "downloaded_at"]
        assert (
            before.loc[name, "updated_by_source"]
            == after.loc[name, "updated_by_source"]
        )
    assert after.loc["leiden", "downloaded_at"] == T1
    assert after.loc["leiden", "updated_by_source"] == "2026-01-13T14:22:43Z"


def test_the_rows_of_eodi_and_leiden_give_the_date_of_the_last_change(ingested, out):
    current = quality.run(ingested, out).set_index("source")

    assert current.loc["leiden", "updated_by_source"] == "2026-01-13T14:22:43Z"
    assert current.loc["eodi", "updated_by_source"] == "2026-11-12T14:17:00Z"


def test_a_source_that_gives_no_date_has_a_blank_updated_by_source(
    store, out, monkeypatch
):
    ingest_at(
        monkeypatch,
        store,
        "eodi",
        0,
        {"all.bin": b"good"},
        normalize=fixture_normalize(),
        updated_by_source=sources.no_date,
    )
    row = quality_row(store, out, "eodi")

    assert row["updated_by_source"] is None
    assert row["downloaded_at"] == T1


def test_a_clean_table_with_no_lineage_gives_blank_dates(store, out):
    assert ingest.read_lineage("eodi", store) is None
    row = quality_row(store, out, "eodi")

    assert row["downloaded_at"] is None
    assert row["updated_by_source"] is None


def test_the_rows_of_an_older_history_get_blank_values_in_the_new_columns(
    store, out, monkeypatch
):
    quality.run(store, out)
    new = ["downloaded_at", "updated_by_source"]
    old = master(store).drop(columns=new)
    store.backend.commit({quality.QUALITY_PATH: quality.to_parquet(old)}, "test")
    ingest_at(
        monkeypatch,
        store,
        "eodi",
        1,
        {"all.bin": b"good"},
        normalize=fixture_normalize(),
    )
    quality.run(store, out)
    table = master(store)

    assert list(table.columns) == quality.COLUMNS
    older = table[table["run_at"] == pd.Timestamp(START, tz="UTC").isoformat()]
    assert len(older) == len(sources.SOURCES)
    assert older[new].isna().all().all()
    newest = table[table["run_at"] == table["run_at"].max()].set_index("source")
    assert newest.loc["eodi", "downloaded_at"] == T2
