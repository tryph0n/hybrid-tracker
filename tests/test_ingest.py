import dataclasses
import io

import pandas as pd
import pytest
from conftest import FIXTURES, recorded_payloads

from tracker import cli, ingest, sources
from tracker.sources import read_events
from tracker.store import LocalBackend, Payload, Store

HEADER = (
    "event_id,source,date,end_date,date_precision,countries,place,lat,lon,"
    "geo_precision,domain,category,subcategory,title,event_status,actors,"
    "actor_name,attribution,attributed_by,url\n"
)

pytestmark = pytest.mark.disable_socket


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(LocalBackend(tmp_path / "store"))


def serve(monkeypatch, name: str, events: pd.DataFrame) -> None:
    """Make source `name` download one payload and map it to `events`."""
    source = dataclasses.replace(
        sources.SOURCES[name],
        extract=lambda: [Payload(name, "all.bin", b"payload", "https://e/all", 200)],
        normalize=lambda files: events,
    )
    monkeypatch.setitem(sources.SOURCES, name, source)


def serve_recorded(monkeypatch, name: str) -> None:
    """Make source `name` download its recorded files instead of the network."""
    files = recorded_payloads(name)
    source = dataclasses.replace(
        sources.SOURCES[name],
        extract=lambda: [
            Payload(name, file, content, f"https://e/{file}", 200)
            for file, content in files.items()
        ],
    )
    monkeypatch.setitem(sources.SOURCES, name, source)


@pytest.fixture
def eodi_events() -> pd.DataFrame:
    return pd.read_csv(
        FIXTURES / "events" / "eodi.csv", keep_default_na=False, na_values=[""]
    )


def test_ingest_stores_a_sorted_deterministic_file(
    data_dir, store, monkeypatch, eodi_events
):
    serve(monkeypatch, "eodi", eodi_events.sample(frac=1, random_state=1))
    ingest.ingest("eodi", store)
    first = (data_dir / "eodi.csv").read_bytes()

    stored = read_events("eodi")
    assert stored["event_id"].is_monotonic_increasing
    expected = eodi_events.sort_values("event_id", ignore_index=True)
    pd.testing.assert_frame_equal(stored, expected)

    serve(monkeypatch, "eodi", eodi_events.sample(frac=1, random_state=2))
    ingest.ingest("eodi", store)
    assert (data_dir / "eodi.csv").read_bytes() == first


def test_ingest_rejects_a_shrunken_source(data_dir, store, monkeypatch, eodi_events):
    serve(monkeypatch, "eodi", eodi_events)
    ingest.ingest("eodi", store)
    before = (data_dir / "eodi.csv").read_bytes()

    serve(monkeypatch, "eodi", eodi_events.head(9))
    with pytest.raises(ValueError, match="9 events, 10 before"):
        ingest.ingest("eodi", store)
    assert (data_dir / "eodi.csv").read_bytes() == before


def test_ingest_rejects_invalid_events(data_dir, store, monkeypatch, eodi_events):
    broken = eodi_events.assign(category="balloon")
    serve(monkeypatch, "eodi", broken)
    with pytest.raises(ValueError, match="Unknown category"):
        ingest.ingest("eodi", store)
    assert not (data_dir / "eodi.csv").exists()


def test_ingest_rejects_a_blank_required_cell(
    data_dir, store, monkeypatch, eodi_events
):
    serve(monkeypatch, "eodi", eodi_events)
    ingest.ingest("eodi", store)
    before = (data_dir / "eodi.csv").read_bytes()

    serve(monkeypatch, "eodi", eodi_events.assign(title=" "))
    with pytest.raises(ValueError, match="without title"):
        ingest.ingest("eodi", store)
    assert (data_dir / "eodi.csv").read_bytes() == before


def test_check_rejects_an_id_shared_by_two_sources(sample_data_dir, caplog):
    caplog.set_level("INFO")
    ingest.check()
    expected = f"{len(sources.SOURCES)} sources: valid"
    assert caplog.text.strip().endswith(expected)

    eodi_text = (sample_data_dir / "eodi.csv").read_text(encoding="utf-8")
    (sample_data_dir / "leiden.csv").write_text(eodi_text, encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate event_id"):
        ingest.check()


def test_read_events_keeps_the_namibia_code(data_dir):
    row = (
        "eodi:X,eodi,2025-01-01,,day,NA,Windhoek,,,place,other,unclassified,x,"
        "Title,confirmed,,,none,,https://e\n"
    )
    (data_dir / "eodi.csv").write_text(HEADER + row, encoding="utf-8")
    events = read_events("eodi")
    assert events.loc[0, "countries"] == "NA"
    assert pd.isna(events.loc[0, "lat"])


@pytest.mark.parametrize("target", ["", "bogus"])
def test_the_command_rejects_an_unknown_target(target, capsys):
    with pytest.raises(SystemExit) as stop:
        cli.main(["ingest", target])
    assert stop.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_run_rejects_an_unknown_target():
    with pytest.raises(ValueError, match="Unknown target 'bogus'"):
        ingest.run("bogus")


def test_the_command_shows_its_help(capsys):
    with pytest.raises(SystemExit) as stop:
        cli.main(["ingest", "--help"])
    assert stop.value.code == 0
    assert "live" in capsys.readouterr().out


def test_the_command_checks_the_stored_files(sample_data_dir, caplog):
    caplog.set_level("INFO")
    cli.main(["ingest", "check"])
    assert "valid" in caplog.text


@pytest.mark.parametrize("name", sources.SOURCES)
def test_normalize_works_offline_on_the_recorded_payloads(name):
    events = sources.SOURCES[name].normalize(recorded_payloads(name))
    ingest.validate(events)
    assert len(events) > 0
    assert set(events["source"]) == {name}


def test_ingest_keeps_the_payloads_and_the_clean_table(data_dir, store, monkeypatch):
    serve_recorded(monkeypatch, "eodi")
    ingest.ingest("eodi", store)

    manifest = store.manifest("eodi")
    assert [line["name"] for line in manifest] == [
        "incidentes.csv",
        "incidentes_sin_ubicacion.json",
    ]
    assert all(line["stored"] and line["status"] == 200 for line in manifest)
    for line in manifest:
        assert (
            store.backend.read(line["path"]) == recorded_payloads("eodi")[line["name"]]
        )

    table = store.backend.read("clean/eodi.parquet")
    assert table is not None
    clean = pd.read_parquet(io.BytesIO(table))
    assert clean.to_csv(index=False, lineterminator="\n") == (
        data_dir / "eodi.csv"
    ).read_text(encoding="utf-8")


def test_ingest_keeps_the_payloads_when_the_mapping_fails(
    data_dir, store, monkeypatch, eodi_events
):
    serve(monkeypatch, "eodi", eodi_events.assign(category="balloon"))
    with pytest.raises(ValueError, match="Unknown category"):
        ingest.ingest("eodi", store)
    assert len(store.payloads("eodi")) == 1
    assert store.backend.read("clean/eodi.parquet") is None


def test_the_shrink_guard_reads_the_clean_table_first(
    data_dir, store, monkeypatch, eodi_events
):
    serve(monkeypatch, "eodi", eodi_events)
    ingest.ingest("eodi", store)
    (data_dir / "eodi.csv").unlink()

    serve(monkeypatch, "eodi", eodi_events.head(9))
    with pytest.raises(ValueError, match="9 events, 10 before"):
        ingest.ingest("eodi", store)


def test_the_shrink_guard_falls_back_to_the_csv_file(
    data_dir, store, monkeypatch, eodi_events
):
    serve(monkeypatch, "eodi", eodi_events)
    ingest.ingest("eodi", store)
    store.backend.root.joinpath("clean/eodi.parquet").unlink()

    serve(monkeypatch, "eodi", eodi_events.head(9))
    with pytest.raises(ValueError, match="9 events, 10 before"):
        ingest.ingest("eodi", store)


def test_run_ingests_every_source_of_a_kind(
    data_dir, tmp_path, monkeypatch, eodi_events
):
    monkeypatch.setenv("TRACKER_STORE", "local")
    monkeypatch.setenv("TRACKER_STORE_DIR", str(tmp_path / "store"))
    serve(monkeypatch, "eodi", eodi_events)
    ingest.run("live")
    assert (data_dir / "eodi.csv").exists()
    assert not (data_dir / "leiden.csv").exists()


def test_registry_matches_the_stored_files():
    stored = {path.stem for path in sources.DATA_DIR.glob("*.csv")}
    assert stored == set(sources.SOURCES)
    for name, source in sources.SOURCES.items():
        assert source.kind in ("live", "static"), name
        events = read_events(name)
        assert set(events["source"]) == {name}, name
        ids = events["event_id"].astype(str)
        assert list(ids[~ids.str.startswith(f"{name}:")]) == [], name
