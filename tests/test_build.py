import dataclasses
import hashlib
import io
import shutil

import duckdb
import pandas as pd
import pytest
from conftest import FIXTURES, GEONAMES_PLACES, ROOT, geonames_archive

from tracker import actors, build, cli, geonames, ingest, sources
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
    for name in sources.SOURCES:
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
        assert sorted(listed["source"]) == sorted(sources.SOURCES)
    total = sum(len(fixture_events(name)) for name in sources.SOURCES)
    assert len(pd.read_parquet(out / "open" / "events.parquet")) == total


def test_two_builds_give_the_same_bytes(store, tmp_path):
    build.build(store, tmp_path / "one")
    build.build(store, tmp_path / "two")

    assert digests(tmp_path / "one") == digests(tmp_path / "two")
    assert len(digests(tmp_path / "one")) == len(FILES) + len(OPEN_FILES)


def test_the_output_does_not_depend_on_the_input_row_order(store, tmp_path):
    build.build(store, tmp_path / "one")
    for name in sources.SOURCES:
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
    given = pd.concat([fixture_events(name) for name in sources.SOURCES])
    given = given[given["lat"].notna()].set_index("event_id")

    assert len(given) > 5
    published = events.loc[given.index, ["lat", "lon", "geo_precision"]]
    pd.testing.assert_frame_equal(
        published, given[["lat", "lon", "geo_precision"]], check_names=False
    )


def test_only_rows_without_source_coordinates_gain_the_place_precision(store, tmp_path):
    events = build_events(store, tmp_path)
    clean = pd.concat([fixture_events(name) for name in sources.SOURCES])
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
    for name in sources.SOURCES:
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
    for name in sources.SOURCES:
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
