import dataclasses
import hashlib
import io
import shutil

import duckdb
import pandas as pd
import pytest
from conftest import FIXTURES

from tracker import build, cli, ingest, sources
from tracker.schema import COLUMNS, read_csv
from tracker.store import LocalBackend, Store

pytestmark = pytest.mark.disable_socket

FILES = ["events.parquet", "events.csv", "sources.parquet"]


def put_clean(store: Store, name: str, events: pd.DataFrame) -> None:
    table = io.BytesIO()
    events.to_parquet(table, index=False)
    store.backend.commit({ingest.clean_path(name): table.getvalue()}, "test")


def fixture_events(name: str) -> pd.DataFrame:
    return read_csv(FIXTURES / "events" / f"{name}.csv")


@pytest.fixture
def store(tmp_path) -> Store:
    store = Store(LocalBackend(tmp_path / "store"))
    for name in sources.SOURCES:
        put_clean(store, name, fixture_events(name))
    return store


@pytest.fixture
def sql_dir(tmp_path):
    folder = tmp_path / "sql"
    shutil.copytree(build.SQL_DIR, folder)
    return folder


def digests(folder):
    return {
        str(file.relative_to(folder)): hashlib.sha256(file.read_bytes()).hexdigest()
        for file in sorted(folder.rglob("*"))
        if file.is_file()
    }


def add_nc_source(monkeypatch, store: Store) -> pd.DataFrame:
    """Register a fake `nc` source with two rows, and return those rows."""
    events = fixture_events("eodi").head(2).copy()
    events["source"] = "fakenc"
    events["event_id"] = ["fakenc:1", "fakenc:2"]
    source = dataclasses.replace(
        sources.SOURCES["eodi"], title="Fake NC", licence="CC BY-NC 4.0", family="nc"
    )
    monkeypatch.setitem(sources.SOURCES, "fakenc", source)
    put_clean(store, "fakenc", events)
    return events


def test_build_writes_the_three_files_of_each_family(store, tmp_path):
    out = tmp_path / "out"
    build.build(store, out)

    for family in sources.FAMILIES:
        assert sorted(p.name for p in (out / family).iterdir()) == sorted(FILES)
        events = pd.read_parquet(out / family / "events.parquet")
        assert list(events.columns) == COLUMNS
        assert len(read_csv(out / family / "events.csv")) == len(events)
        assert events["event_id"].is_unique
        listed = pd.read_parquet(out / family / "sources.parquet")
        assert sorted(listed["source"]) == sorted(sources.SOURCES)
    total = sum(len(fixture_events(name)) for name in sources.SOURCES)
    assert len(pd.read_parquet(out / "open" / "events.parquet")) == total


def test_two_builds_give_the_same_bytes(store, tmp_path):
    build.build(store, tmp_path / "one")
    build.build(store, tmp_path / "two")

    assert digests(tmp_path / "one") == digests(tmp_path / "two")
    assert len(digests(tmp_path / "one")) == 2 * len(FILES)


def test_the_output_does_not_depend_on_the_input_row_order(store, tmp_path):
    build.build(store, tmp_path / "one")
    for name in sources.SOURCES:
        shuffled = fixture_events(name).sample(frac=1, random_state=3)
        put_clean(store, name, shuffled)
    build.build(store, tmp_path / "two")

    assert digests(tmp_path / "one") == digests(tmp_path / "two")


def test_a_lost_row_fails_the_build_and_names_the_source(store, sql_dir, tmp_path):
    victim = fixture_events("leiden")["event_id"].iloc[0]
    (sql_dir / "04_lose_a_row.sql").write_text(
        f"DELETE FROM events_open WHERE event_id = '{victim}';\n"
        f"DELETE FROM events_nc WHERE event_id = '{victim}';\n"
    )
    out = tmp_path / "out"

    with pytest.raises(ValueError, match="leiden"):
        build.build(store, out, sql_dir)
    assert not out.exists()


def test_a_row_lost_by_the_union_fails_the_build(store, sql_dir, tmp_path):
    (sql_dir / "01_union.sql").write_text(
        "CREATE TABLE events AS SELECT clean.*, registry.family FROM clean "
        "JOIN registry USING (source) WHERE source <> 'eodi';\n"
    )

    with pytest.raises(ValueError, match="eodi"):
        build.build(store, tmp_path / "out", sql_dir)


def test_an_output_that_breaks_the_schema_fails_the_build(store, sql_dir, tmp_path):
    (sql_dir / "04_break.sql").write_text(
        "ALTER TABLE events_open ALTER lat TYPE VARCHAR;\n"
    )
    out = tmp_path / "out"

    with pytest.raises(ValueError, match="breaks the schema"):
        build.build(store, out, sql_dir)
    assert not out.exists()


def test_a_value_outside_the_schema_fails_the_build(store, sql_dir, tmp_path):
    (sql_dir / "04_break.sql").write_text(
        "UPDATE events_nc SET domain = 'bogus' WHERE source = 'eodi';\n"
    )

    with pytest.raises(ValueError, match="domain"):
        build.build(store, tmp_path / "out", sql_dir)


def test_a_failed_build_keeps_the_previous_output(store, sql_dir, tmp_path):
    out = tmp_path / "out"
    build.build(store, out)
    before = digests(out)
    (sql_dir / "04_break.sql").write_text(
        "ALTER TABLE events_open ALTER lat TYPE VARCHAR;\n"
    )

    with pytest.raises(ValueError):
        build.build(store, out, sql_dir)
    assert digests(out) == before


def test_a_failing_sql_file_fails_the_build(store, sql_dir, tmp_path):
    (sql_dir / "04_bad.sql").write_text("SELECT * FROM missing_table;\n")

    with pytest.raises(duckdb.Error):
        build.build(store, tmp_path / "out", sql_dir)


def test_a_nc_source_is_published_in_the_nc_family_only(store, tmp_path, monkeypatch):
    fake = add_nc_source(monkeypatch, store)
    out = tmp_path / "out"
    build.build(store, out)

    open_events = pd.read_parquet(out / "open" / "events.parquet")
    nc_events = pd.read_parquet(out / "nc" / "events.parquet")
    assert "fakenc" not in set(open_events["source"])
    assert set(fake["event_id"]) <= set(nc_events["event_id"])
    assert len(nc_events) == len(open_events) + len(fake)
    open_sources = pd.read_parquet(out / "open" / "sources.parquet")
    nc_sources = pd.read_parquet(out / "nc" / "sources.parquet")
    assert "fakenc" not in set(open_sources["source"])
    assert "fakenc" in set(nc_sources["source"])
    assert "fakenc:1" not in (out / "open" / "events.csv").read_text()
    assert "fakenc:1" in (out / "nc" / "events.csv").read_text()


def test_a_source_without_a_clean_table_fails_the_build(store, tmp_path):
    (store.backend.root / ingest.clean_path("leiden")).unlink()

    with pytest.raises(ValueError, match="leiden"):
        build.build(store, tmp_path / "out")


def test_an_unknown_family_fails_the_build(store, tmp_path, monkeypatch):
    bad = dataclasses.replace(sources.SOURCES["eodi"], family="paid")
    monkeypatch.setitem(sources.SOURCES, "eodi", bad)

    with pytest.raises(ValueError, match="paid"):
        build.build(store, tmp_path / "out")


def test_a_folder_without_sql_fails_the_build(store, tmp_path):
    (tmp_path / "empty").mkdir()

    with pytest.raises(ValueError, match="No SQL file"):
        build.build(store, tmp_path / "out", tmp_path / "empty")


def test_every_source_has_a_known_family():
    for source in sources.SOURCES.values():
        assert source.family in sources.FAMILIES


def test_the_build_command_writes_the_tables(store, tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKER_STORE", "local")
    monkeypatch.setenv("TRACKER_STORE_DIR", str(store.backend.root))
    out = tmp_path / "out"

    cli.main(["build", "--out", str(out)])

    assert (out / "open" / "events.parquet").exists()
    assert (out / "nc" / "sources.parquet").exists()


def test_missing_values_stay_missing_in_the_csv(store, tmp_path):
    out = tmp_path / "out"
    build.build(store, out)

    csv = read_csv(out / "open" / "events.csv")
    parquet = pd.read_parquet(out / "open" / "events.parquet")
    assert csv["lat"].isna().tolist() == parquet["lat"].isna().tolist()
    assert csv["end_date"].isna().tolist() == parquet["end_date"].isna().tolist()
    assert csv["lat"].isna().any()
