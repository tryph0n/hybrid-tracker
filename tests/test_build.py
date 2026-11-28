import dataclasses
import hashlib
import io
import shutil
from datetime import date

import duckdb
import pandas as pd
import pytest
from conftest import EVENT_SOURCES, FIXTURES, GEONAMES_PLACES, ROOT, geonames_archive
from test_stanford import FIXTURE, seed

from tracker import actors, build, cli, geonames, ingest, publish, sources, stanford
from tracker.errors import UserError
from tracker.schema import PUBLISHED_COLUMNS, read_csv
from tracker.store import LocalBackend, Payload, Store

pytestmark = pytest.mark.disable_socket

FILES = ["events.parquet", "events.csv", "sources.parquet"]
OPEN_FILES = [*FILES, "threat_actors.parquet"]


def put_clean(store: Store, name: str, events: pd.DataFrame) -> None:
    table = io.BytesIO()
    events.to_parquet(table, index=False)
    store.backend.commit({ingest.clean_path(name): table.getvalue()}, "test")


def fixture_events(name: str) -> pd.DataFrame:
    return read_csv(FIXTURES / "events" / f"{name}.csv")


def put_geonames(store: Store, places: list[dict] | None = None) -> None:
    archive = geonames_archive(places)
    store.save(Payload("geonames", geonames.FILE_NAME, archive, geonames.URL, 200))


def put_actors(store: Store, content: bytes | None = None) -> None:
    content = content or (FIXTURES / "misp_threat_actor.json").read_bytes()
    store.save(Payload(actors.SOURCE, actors.FILE_NAME, content, actors.URL, 200))


@pytest.fixture
def store(tmp_path) -> Store:
    store = Store(LocalBackend(tmp_path / "store"))
    for name in EVENT_SOURCES:
        put_clean(store, name, fixture_events(name))
    put_geonames(store)
    put_actors(store)
    return store


@pytest.fixture
def sql_dir(tmp_path):
    folder = tmp_path / "sql"
    shutil.copytree(build.SQL_DIR, folder)
    return folder


CURATION_HEADER = "event_id_a,event_id_b,reason\n"


@pytest.fixture(autouse=True)
def curation_dir(tmp_path, monkeypatch):
    """Empty curation files: the real ones name rows that the samples do not have."""
    folder = tmp_path / "curation"
    folder.mkdir()
    for name in ("merges.csv", "splits.csv"):
        (folder / name).write_text(CURATION_HEADER, encoding="utf-8")
    (folder / "actors.csv").write_text(
        ",".join(actors.CURATION_COLUMNS) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(build, "CURATION_DIR", folder)
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
        expected = OPEN_FILES if family == "open" else FILES
        assert sorted(p.name for p in (out / family).iterdir()) == sorted(expected)
        events = pd.read_parquet(out / family / "events.parquet")
        assert list(events.columns) == PUBLISHED_COLUMNS
        assert len(read_csv(out / family / "events.csv")) == len(events)
        assert events["event_id"].is_unique
        listed = pd.read_parquet(out / family / "sources.parquet")
        assert sorted(listed["source"]) == sorted(EVENT_SOURCES)
    total = sum(len(fixture_events(name)) for name in EVENT_SOURCES)
    assert len(pd.read_parquet(out / "open" / "events.parquet")) == total


def test_two_builds_give_the_same_bytes(store, tmp_path):
    build.build(store, tmp_path / "one")
    build.build(store, tmp_path / "two")

    assert digests(tmp_path / "one") == digests(tmp_path / "two")
    assert len(digests(tmp_path / "one")) == len(FILES) + len(OPEN_FILES)


def test_the_output_does_not_depend_on_the_input_row_order(store, tmp_path):
    build.build(store, tmp_path / "one")
    for name in EVENT_SOURCES:
        shuffled = fixture_events(name).sample(frac=1, random_state=3)
        put_clean(store, name, shuffled)
    build.build(store, tmp_path / "two")

    assert digests(tmp_path / "one") == digests(tmp_path / "two")


def test_a_lost_row_fails_the_build_and_names_the_source(store, sql_dir, tmp_path):
    victim = fixture_events("leiden")["event_id"].iloc[0]
    (sql_dir / "05_lose_a_row.sql").write_text(
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
    (sql_dir / "05_break.sql").write_text(
        "ALTER TABLE events_open ALTER lat TYPE VARCHAR;\n"
    )
    out = tmp_path / "out"

    with pytest.raises(ValueError, match="breaks the schema"):
        build.build(store, out, sql_dir)
    assert not out.exists()


def test_a_value_outside_the_schema_fails_the_build(store, sql_dir, tmp_path):
    (sql_dir / "05_break.sql").write_text(
        "UPDATE events_nc SET domain = 'bogus' WHERE source = 'eodi';\n"
    )

    with pytest.raises(ValueError, match="domain"):
        build.build(store, tmp_path / "out", sql_dir)


def test_a_failed_build_keeps_the_previous_output(store, sql_dir, tmp_path):
    out = tmp_path / "out"
    build.build(store, out)
    before = digests(out)
    (sql_dir / "05_break.sql").write_text(
        "ALTER TABLE events_open ALTER lat TYPE VARCHAR;\n"
    )

    with pytest.raises(ValueError):
        build.build(store, out, sql_dir)
    assert digests(out) == before


def test_a_failing_sql_file_fails_the_build(store, sql_dir, tmp_path):
    (sql_dir / "05_bad.sql").write_text("SELECT * FROM missing_table;\n")

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


def test_a_source_without_a_clean_table_is_left_out(
    store, tmp_path, monkeypatch, caplog
):
    (store.backend.root / ingest.clean_path("leiden")).unlink()
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("TRACKER_STORE", "local")
    monkeypatch.setenv("TRACKER_STORE_DIR", str(store.backend.root))
    out = tmp_path / "out"

    caplog.set_level("INFO")
    cli.main(["build", "--out", str(out)])

    assert "`leiden`" in summary.read_text(encoding="utf-8")
    for family in sources.FAMILIES:
        events = pd.read_parquet(out / family / "events.parquet")
        assert set(events["source"]) == {"eodi"}
        assert len(events) == len(fixture_events("eodi"))
        assert "leiden" not in read_csv(out / family / "events.csv")["source"].tolist()
        listed = pd.read_parquet(out / family / "sources.parquet")
        assert list(listed["source"]) == ["eodi"]
    assert "open: 1 sources" in caplog.text
    assert "nc: 1 sources" in caplog.text


def test_the_card_of_a_build_without_a_source_does_not_name_it(store, tmp_path):
    add_context_source(store, CONTEXT_DAYS)
    (store.backend.root / ingest.clean_path("leiden")).unlink()
    out = tmp_path / "out"
    left_out = build.build(store, out)

    assert left_out == ["leiden"]
    leiden = sources.SOURCES["leiden"]
    for family in sources.FAMILIES:
        published = pd.read_parquet(out / family / "sources.parquet")["source"]
        card = publish.render_card(family, set(FILES), published=list(published))
        assert sources.SOURCES["eodi"].citation in card
        assert leiden.citation not in card
        assert leiden.homepage not in card
        assert "all sources" not in card
        assert "(one row per source)" not in card


def test_a_build_with_every_clean_table_leaves_out_nothing(store, tmp_path):
    add_context_source(store, CONTEXT_DAYS)
    assert build.build(store, tmp_path / "out") == []


def test_a_family_with_no_built_source_is_written_empty_and_named(
    store, tmp_path, monkeypatch
):
    add_nc_source(monkeypatch, store)
    for name in ("eodi", "leiden"):
        (store.backend.root / ingest.clean_path(name)).unlink()
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("TRACKER_STORE", "local")
    monkeypatch.setenv("TRACKER_STORE_DIR", str(store.backend.root))
    out = tmp_path / "out"

    cli.main(["build", "--out", str(out)])

    assert len(pd.read_parquet(out / "open" / "events.parquet")) == 0
    assert len(pd.read_parquet(out / "nc" / "events.parquet")) == 2
    text = summary.read_text(encoding="utf-8")
    assert "- `open`" in text
    assert "- `nc`" not in text


def test_a_build_with_no_clean_table_fails(store, tmp_path):
    for name in EVENT_SOURCES:
        (store.backend.root / ingest.clean_path(name)).unlink()

    with pytest.raises(ValueError, match="No source has a clean table"):
        build.build(store, tmp_path / "out")
    assert not (tmp_path / "out").exists()


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
    for source in EVENT_SOURCES.values():
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


def unlocated(event_id: str, place: str, countries: str, precision: str = "place"):
    """A copy of the first EODI fixture row, with no coordinates."""
    row = fixture_events("eodi").iloc[[0]].copy()
    row["event_id"] = f"eodi:{event_id}"
    row["place"] = place
    row["countries"] = countries
    row["lat"] = float("nan")
    row["lon"] = float("nan")
    row["geo_precision"] = precision
    return row


def build_events(store: Store, tmp_path, name="out") -> pd.DataFrame:
    build.build(store, tmp_path / name)
    return pd.read_parquet(tmp_path / name / "open" / "events.parquet").set_index(
        "event_id"
    )


def test_a_town_without_coordinates_gets_those_of_geonames(store, tmp_path):
    events = build_events(store, tmp_path)

    arna = events.loc["eodi:EODI-2025-00140"]
    assert (arna["lat"], arna["lon"], arna["geo_precision"]) == (60.42, 5.46, "place")
    assert arna["place"] == "Arna, Norway"


def test_a_region_that_is_not_a_town_stays_empty(store, tmp_path):
    events = build_events(store, tmp_path)

    gagauzia = events.loc["eodi:EODI-2025-00212"]
    assert pd.isna(gagauzia["lat"]) and pd.isna(gagauzia["lon"])
    assert gagauzia["geo_precision"] == "place"


def test_a_source_that_gives_coordinates_is_never_overwritten(store, tmp_path):
    put_geonames(
        store,
        [
            *GEONAMES_PLACES,
            {"id": 3, "name": "Warsaw", "country": "PL", "lat": "1", "lon": "1"},
            {"id": 4, "name": "Vught", "country": "NL", "lat": "2", "lon": "2"},
        ],
    )
    events = build_events(store, tmp_path)
    given = pd.concat([fixture_events(name) for name in EVENT_SOURCES])
    given = given[given["lat"].notna()].set_index("event_id")

    assert len(given) > 5
    published = events.loc[given.index, ["lat", "lon", "geo_precision"]]
    pd.testing.assert_frame_equal(
        published, given[["lat", "lon", "geo_precision"]], check_names=False
    )


def test_only_rows_without_source_coordinates_gain_the_place_precision(store, tmp_path):
    events = build_events(store, tmp_path)
    clean = pd.concat([fixture_events(name) for name in EVENT_SOURCES])
    clean = clean.set_index("event_id")

    gained = events[events["lat"].notna() & clean.loc[events.index, "lat"].isna()]
    assert list(gained.index) == ["eodi:EODI-2025-00140"]
    assert set(gained["geo_precision"]) == {"place"}


def test_a_country_a_sea_and_a_coast_are_not_matched_as_towns(store, tmp_path):
    put_clean(
        store,
        "eodi",
        pd.concat(
            [
                fixture_events("eodi"),
                unlocated("sea", "Aegean Sea, Greece", "GR", "sea"),
                unlocated("country", "Poland, Poland", "PL", "country"),
                unlocated("coast", "Romanian coast, Romania", "RO"),
            ],
            ignore_index=True,
        ),
    )
    put_geonames(
        store,
        [
            {"id": 3, "name": "Aegean Sea", "country": "GR", "lat": "1", "lon": "1"},
            {"id": 4, "name": "Poland", "country": "PL", "lat": "2", "lon": "2"},
            {"id": 5, "name": "Estonia", "country": "EE", "lat": "3", "lon": "3"},
        ],
    )
    events = build_events(store, tmp_path)

    for event_id in ["sea", "country", "coast"]:
        assert pd.isna(events.loc[f"eodi:{event_id}", "lat"])
    assert pd.isna(events.loc["leiden:004", "lat"])


def test_the_town_must_be_in_the_country_of_the_event(store, tmp_path):
    put_clean(
        store,
        "eodi",
        pd.concat(
            [
                fixture_events("eodi"),
                unlocated("other", "Arna, Sweden", "SE"),
                unlocated("two", "Arna, Norway", "NO;SE"),
                unlocated("none", "Arna, Norway", ""),
            ],
            ignore_index=True,
        ),
    )
    events = build_events(store, tmp_path)

    for event_id in ["other", "two", "none"]:
        assert pd.isna(events.loc[f"eodi:{event_id}", "lat"])


def test_a_diacritic_or_case_difference_still_matches(store, tmp_path):
    put_clean(
        store,
        "eodi",
        pd.concat(
            [fixture_events("eodi"), unlocated("fold", "ARNÁ, Norway", "NO")],
            ignore_index=True,
        ),
    )
    events = build_events(store, tmp_path)

    assert events.loc["eodi:fold", "lat"] == 60.42


def test_an_ambiguous_name_gives_the_same_coordinates_whatever_the_file_order(
    store, tmp_path
):
    twins = [
        {"id": 5, "name": "Arna", "country": "NO", "lat": "10", "lon": "10"},
        {"id": 6, "name": "Arna", "country": "NO", "lat": "11", "lon": "11"},
        {
            "id": 7,
            "name": "Arna",
            "country": "NO",
            "lat": "12",
            "lon": "12",
            "population": 50000,
        },
    ]
    put_geonames(store, twins)
    first = build_events(store, tmp_path, "one")
    put_geonames(store, twins[::-1])
    second = build_events(store, tmp_path, "two")

    assert first.loc["eodi:EODI-2025-00140", "lat"] == 12
    assert digests(tmp_path / "one") == digests(tmp_path / "two")
    assert first.equals(second)


def test_the_build_fails_without_a_geonames_file(tmp_path):
    store = Store(LocalBackend(tmp_path / "empty"))
    for name in EVENT_SOURCES:
        put_clean(store, name, fixture_events(name))

    with pytest.raises(ValueError, match="geonames.*ingest geonames"):
        build.build(store, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_the_open_family_gets_the_threat_actors_table_and_the_nc_family_does_not(
    store, tmp_path, curation_dir
):
    shutil.copyfile(ROOT / "curation" / "actors.csv", curation_dir / "actors.csv")
    out = tmp_path / "out"
    build.build(store, out)

    table = pd.read_parquet(out / "open" / "threat_actors.parquet")
    assert list(table.columns) == actors.COLUMNS
    assert len(table) == 7
    assert table["actor"].is_unique
    assert table.set_index("actor").loc["NoName057(16)", "country"] == "RU"
    assert table.set_index("actor").loc["Killnet", "country"] == "RU"
    assert not (out / "nc" / "threat_actors.parquet").exists()


def test_the_build_fails_without_a_misp_galaxy_file(tmp_path):
    store = Store(LocalBackend(tmp_path / "empty"))
    for name in EVENT_SOURCES:
        put_clean(store, name, fixture_events(name))
    put_geonames(store)

    with pytest.raises(ValueError, match="misp_galaxy.*ingest misp_galaxy"):
        build.build(store, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_the_threat_actors_never_change_the_events(store, tmp_path):
    build.build(store, tmp_path / "one")
    other = (FIXTURES / "misp_threat_actor.json").read_bytes().replace(b'"RU"', b'"IR"')
    put_actors(store, other)
    build.build(store, tmp_path / "two")

    one, two = digests(tmp_path / "one"), digests(tmp_path / "two")
    assert one["open/threat_actors.parquet"] != two["open/threat_actors.parquet"]
    for name in ("events.parquet", "events.csv", "sources.parquet"):
        for family in sources.FAMILIES:
            assert one[f"{family}/{name}"] == two[f"{family}/{name}"]


def test_the_build_logs_the_digest_and_the_date_of_the_geonames_file(
    store, tmp_path, caplog
):
    caplog.set_level("INFO")
    build.build(store, tmp_path / "out")

    digest = hashlib.sha256(geonames_archive()).hexdigest()
    line = store.payloads("geonames")[-1]
    assert f"sha256 {digest}, fetched {line['fetched_at']}" in caplog.text


def test_a_change_of_source_coordinates_fails_the_build(store, sql_dir, tmp_path):
    (sql_dir / "02_geocode.sql").write_text(
        "UPDATE events SET lat = lat + 1 WHERE event_id = 'eodi:EODI-2025-00001';\n"
    )

    with pytest.raises(ValueError, match="1 rows with source coordinates"):
        build.build(store, tmp_path / "out", sql_dir)


def test_a_new_coordinate_without_the_place_precision_fails_the_build(
    store, sql_dir, tmp_path
):
    (sql_dir / "02_geocode.sql").write_text(
        "UPDATE events SET lat = 1, lon = 1, geo_precision = 'exact' "
        "WHERE event_id = 'eodi:EODI-2025-00140';\n"
    )

    with pytest.raises(ValueError, match="without `place` precision"):
        build.build(store, tmp_path / "out", sql_dir)


def write_curation(folder, name: str, *pairs: tuple[str, str]) -> None:
    lines = "".join(f"{a},{b},test\n" for a, b in pairs)
    (folder / name).write_text(CURATION_HEADER + lines, encoding="utf-8")


def test_each_cluster_has_one_canonical_row_in_each_family(store, tmp_path):
    out = tmp_path / "out"
    build.build(store, out)

    for family in sources.FAMILIES:
        events = pd.read_parquet(out / family / "events.parquet")
        clusters = events.groupby("cluster_id")
        assert (clusters["is_canonical"].sum() == 1).all()
        assert (
            events["n_sources"]
            == events["cluster_id"].map(clusters["source"].nunique())
        ).all()


def test_a_merge_line_joins_two_rows_and_a_split_line_keeps_them_apart(
    store, tmp_path, curation_dir
):
    eodi = fixture_events("eodi")["event_id"].iloc[0]
    leiden = fixture_events("leiden")["event_id"].iloc[0]
    write_curation(curation_dir, "merges.csv", (eodi, leiden))
    build.build(store, tmp_path / "merged")
    write_curation(curation_dir, "merges.csv")
    write_curation(curation_dir, "splits.csv", (eodi, leiden))
    build.build(store, tmp_path / "split")

    merged = pd.read_parquet(tmp_path / "merged" / "open" / "events.parquet")
    row = merged.set_index("event_id")
    assert row.loc[eodi, "cluster_id"] == row.loc[leiden, "cluster_id"] == eodi
    assert row.loc[eodi, "n_sources"] == 2
    assert row.loc[eodi, "is_canonical"] and not row.loc[leiden, "is_canonical"]
    split = pd.read_parquet(tmp_path / "split" / "open" / "events.parquet")
    assert (split["n_sources"] == 1).all()
    assert split["is_canonical"].all()


@pytest.mark.parametrize("name", ["merges.csv", "splits.csv"])
def test_a_stale_curation_line_fails_the_build(store, tmp_path, curation_dir, name):
    eodi = fixture_events("eodi")["event_id"].iloc[0]
    write_curation(curation_dir, name, (eodi, "leiden:gone"))

    with pytest.raises(ValueError, match="unknown event ids.*leiden:gone"):
        build.build(store, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_the_open_clusters_use_no_row_of_a_nc_source(
    store, tmp_path, monkeypatch, curation_dir
):
    fake = add_nc_source(monkeypatch, store)
    first, second = fake["event_id"]
    eodi = fixture_events("eodi")["event_id"].iloc[:2]
    write_curation(
        curation_dir, "merges.csv", (eodi.iloc[0], first), (eodi.iloc[1], second)
    )
    build.build(store, tmp_path / "out")

    nc = pd.read_parquet(tmp_path / "out" / "nc" / "events.parquet").set_index(
        "event_id"
    )
    open_ = pd.read_parquet(tmp_path / "out" / "open" / "events.parquet").set_index(
        "event_id"
    )
    assert nc.loc[eodi.iloc[0], "cluster_id"] == eodi.iloc[0]
    assert nc.loc[first, "cluster_id"] == eodi.iloc[0]
    assert nc.loc[eodi.iloc[0], "n_sources"] == 2
    assert open_.loc[eodi.iloc[0], "cluster_id"] == eodi.iloc[0]
    assert open_.loc[eodi.iloc[0], "n_sources"] == 1
    assert open_.loc[eodi.iloc[0], "is_canonical"]
    assert set(open_["cluster_id"]) <= set(open_.index)


def test_a_stored_table_without_drone_presence_is_read_as_empty(store, tmp_path):
    put_clean(store, "leiden", fixture_events("leiden").drop(columns="drone_presence"))

    events = build_events(store, tmp_path)

    leiden = events[events["source"] == "leiden"]
    assert len(leiden) == len(fixture_events("leiden"))
    assert leiden["drone_presence"].isna().all()
    eodi_rows = events[events["source"] == "eodi"]
    expected = fixture_events("eodi").set_index("event_id")["drone_presence"]
    assert eodi_rows["drone_presence"].to_dict() == expected.to_dict()


def test_a_build_on_tables_that_all_lack_drone_presence_gives_an_empty_column(
    store, tmp_path
):
    for name in EVENT_SOURCES:
        put_clean(store, name, fixture_events(name).drop(columns="drone_presence"))

    events = build_events(store, tmp_path)

    assert list(events.reset_index().columns) == PUBLISHED_COLUMNS
    assert events["drone_presence"].isna().all()


def test_a_stored_table_without_a_required_column_fails_the_build(store, tmp_path):
    put_clean(store, "leiden", fixture_events("leiden").drop(columns="title"))

    with pytest.raises(ValueError, match="without title"):
        build_events(store, tmp_path)


CONTEXT_CITATION = sources.SOURCES[stanford.SOURCE].citation


def add_context_source(store: Store, days: list[date]) -> None:
    """Store the month files of the context source, as a run does."""
    seed(store, {day: FIXTURE for day in days})
    stanford.write_months(store, {stanford.month_of(day) for day in days})


MONTHS = ["2026-10", "2026-11"]
CONTEXT_DAYS = [date(2026, 10, 31), date(2026, 11, 1)]


def test_a_context_table_is_in_the_nc_build_folder_only_and_not_in_the_events(
    store, tmp_path
):
    add_context_source(store, CONTEXT_DAYS)
    out = tmp_path / "out"

    left_out = build.build(store, out)
    again = build.build(store, out)

    assert left_out == again == []
    folder = out / "nc" / stanford.TABLE
    assert sorted(path.name for path in folder.iterdir()) == [
        f"{month}.parquet" for month in MONTHS
    ]
    for month in MONTHS:
        stored = store.backend.read(stanford.month_path(month))
        assert (folder / f"{month}.parquet").read_bytes() == stored
    assert not (out / "open" / stanford.TABLE).exists()
    assert sorted(path.name for path in (out / "open").iterdir()) == sorted(OPEN_FILES)
    for family in sources.FAMILIES:
        events = pd.read_parquet(out / family / "events.parquet")
        assert stanford.SOURCE not in set(events["source"])
        assert len(events) == sum(len(fixture_events(name)) for name in fixture_names())


def test_a_context_source_has_a_row_in_the_nc_sources_table_only(store, tmp_path):
    build.build(store, tmp_path / "before")
    add_context_source(store, CONTEXT_DAYS)

    build.build(store, tmp_path / "out")

    before, out = tmp_path / "before", tmp_path / "out"
    listed = pd.read_parquet(out / "nc" / "sources.parquet")
    row = listed[listed["source"] == stanford.SOURCE]
    assert row[["title", "licence", "citation"]].values.tolist() == [
        [sources.SOURCES[stanford.SOURCE].title, "CC BY-NC 4.0", CONTEXT_CITATION]
    ]
    others = listed[listed["source"] != stanford.SOURCE].reset_index(drop=True)
    assert others.equals(pd.read_parquet(before / "nc" / "sources.parquet"))
    credit_lines = "\n".join(publish.credits("nc", listed["source"]))
    assert f"- {CONTEXT_CITATION} CC BY-NC 4.0." in credit_lines
    open_sources = "open/sources.parquet"
    assert (out / open_sources).read_bytes() == (before / open_sources).read_bytes()


def test_the_source_count_in_the_log_includes_the_context_source_of_nc_only(
    store, tmp_path, caplog
):
    build.build(store, tmp_path / "before")
    add_context_source(store, CONTEXT_DAYS)
    caplog.set_level("INFO")

    build.build(store, tmp_path / "out")

    listed = pd.read_parquet(tmp_path / "out" / "nc" / "sources.parquet")
    assert f"nc: {len(listed)} sources" in caplog.text
    open_listed = pd.read_parquet(tmp_path / "out" / "open" / "sources.parquet")
    assert f"open: {len(open_listed)} sources" in caplog.text
    assert stanford.SOURCE in set(listed["source"])
    assert stanford.SOURCE not in set(open_listed["source"])


def fixture_names() -> list[str]:
    return [name for name in sources.SOURCES if name != stanford.SOURCE]


def test_a_context_source_with_no_month_file_is_left_out(store, tmp_path):
    add_context_source(store, [])

    left_out = build.build(store, tmp_path / "out")

    assert left_out == [stanford.SOURCE]
    assert not (tmp_path / "out" / "nc" / stanford.TABLE).exists()


def test_a_month_file_with_other_columns_fails_the_build_and_writes_nothing(
    store, tmp_path
):
    add_context_source(store, CONTEXT_DAYS)
    wrong = io.BytesIO()
    pd.DataFrame({"day": [CONTEXT_DAYS[0]]}).to_parquet(wrong, index=False)
    store.backend.commit({stanford.month_path("2026-10"): wrong.getvalue()}, "test")

    with pytest.raises(UserError, match="stanford: month file 2026-10 breaks"):
        build.build(store, tmp_path / "out")

    assert not (tmp_path / "out").exists()
