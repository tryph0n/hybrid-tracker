import dataclasses
import io

import pandas as pd
import pytest
from conftest import FIXTURES

from tracker import cli, clock, ingest, quality, sources
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


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch, tmp_path):
    set_clock(monkeypatch, START)
    monkeypatch.delenv(quality.SUMMARY_VAR, raising=False)


@pytest.fixture
def store(tmp_path) -> Store:
    store = Store(LocalBackend(tmp_path / "store"))
    for name in sources.SOURCES:
        put_clean(store, name, fixture_events(name))
        fetch(store, name)
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

    for table in [
        master(store),
        pd.read_parquet(out / "nc" / "quality.parquet"),
        pd.read_parquet(out / "open" / "quality.parquet"),
    ]:
        counts = table.groupby("run_at")["source"].apply(sorted)
        assert len(counts) == 2
        assert all(names == sorted(sources.SOURCES) for names in counts)
    assert list(master(store).columns) == quality.COLUMNS


def test_first_run_adds_every_row_and_removes_none(store, out):
    quality.run(store, out)

    for row in master(store).itertuples():
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


def test_live_source_with_no_row_fails_and_names_it(store, out):
    empty = fixture_events("eodi").iloc[0:0]
    put_clean(store, "eodi", empty)

    with pytest.raises(ValueError, match="eodi: no row"):
        quality.run(store, out)
    assert (out / "open" / "quality.parquet").exists()
    assert master(store).set_index("source").loc["eodi", "rows"] == 0


def test_live_source_fetched_four_days_ago_fails_and_static_passes(
    tmp_path, out, monkeypatch
):
    store = Store(LocalBackend(tmp_path / "store"))
    for name in sources.SOURCES:
        put_clean(store, name, fixture_events(name))
    fetch(store, "eodi")
    fetch(store, "leiden")
    set_clock(monkeypatch, days_after(4))
    with pytest.raises(ValueError) as error:
        quality.run(store, out)

    assert "eodi: no successful fetch since" in str(error.value)
    assert "leiden" not in str(error.value)

    fetch(store, "eodi")
    quality.run(store, out)


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


def test_live_source_without_a_successful_fetch_fails(tmp_path, out):
    store = Store(LocalBackend(tmp_path / "store"))
    for name in sources.SOURCES:
        put_clean(store, name, fixture_events(name))
    fetch(store, "eodi", status=503)

    with pytest.raises(ValueError, match="eodi: no successful fetch"):
        quality.run(store, out)


def test_source_without_a_clean_table_fails(tmp_path, out):
    store = Store(LocalBackend(tmp_path / "store"))

    with pytest.raises(ValueError, match="no clean table"):
        quality.run(store, out)


def test_cli_runs_quality(store, out, tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKER_STORE", "local")
    monkeypatch.setenv("TRACKER_STORE_DIR", str(tmp_path / "store"))
    cli.main(["quality", "--out", str(out)])

    assert (out / "open" / "quality.parquet").exists()
