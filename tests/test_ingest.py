import dataclasses
import hashlib
import io
import json

import pandas as pd
import pytest
from conftest import FIXTURES, recorded_payloads

from tracker import cli, clock, eodi, ingest, sources
from tracker.schema import read_csv
from tracker.store import LocalBackend, Payload, Store, open_store

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
        updated_by_source=sources.no_date,
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


def clean_table(store: Store, name: str) -> pd.DataFrame:
    table = store.backend.read(ingest.clean_path(name))
    assert table is not None
    return pd.read_parquet(io.BytesIO(table))


def clean_bytes(store: Store, name: str) -> bytes | None:
    return store.backend.read(ingest.clean_path(name))


def test_ingest_stores_a_sorted_deterministic_file(store, monkeypatch, eodi_events):
    serve(monkeypatch, "eodi", eodi_events.sample(frac=1, random_state=1))
    ingest.ingest("eodi", store)
    first = clean_bytes(store, "eodi")

    stored = clean_table(store, "eodi")
    assert stored["event_id"].is_monotonic_increasing
    expected = eodi_events.sort_values("event_id", ignore_index=True)
    pd.testing.assert_frame_equal(stored, expected)

    serve(monkeypatch, "eodi", eodi_events.sample(frac=1, random_state=2))
    ingest.ingest("eodi", store)
    assert clean_bytes(store, "eodi") == first


def test_ingest_rejects_a_shrunken_source(store, monkeypatch, eodi_events):
    serve(monkeypatch, "eodi", eodi_events)
    ingest.ingest("eodi", store)
    before = clean_bytes(store, "eodi")

    serve(monkeypatch, "eodi", eodi_events.head(9))
    with pytest.raises(ValueError, match="9 events, 10 before"):
        ingest.ingest("eodi", store)
    assert clean_bytes(store, "eodi") == before


def test_ingest_rejects_invalid_events(store, monkeypatch, eodi_events):
    broken = eodi_events.assign(category="balloon")
    serve(monkeypatch, "eodi", broken)
    with pytest.raises(ValueError, match="Unknown category"):
        ingest.ingest("eodi", store)
    assert clean_bytes(store, "eodi") is None


def test_ingest_rejects_a_blank_required_cell(store, monkeypatch, eodi_events):
    serve(monkeypatch, "eodi", eodi_events)
    ingest.ingest("eodi", store)
    before = clean_bytes(store, "eodi")

    serve(monkeypatch, "eodi", eodi_events.assign(title=" "))
    with pytest.raises(ValueError, match="without title"):
        ingest.ingest("eodi", store)
    assert clean_bytes(store, "eodi") == before


def test_read_csv_keeps_the_namibia_code(tmp_path):
    row = (
        "eodi:X,eodi,2025-01-01,,day,NA,Windhoek,,,place,other,unclassified,x,"
        "Title,confirmed,,,none,,https://e\n"
    )
    path = tmp_path / "eodi.csv"
    path.write_text(HEADER + row, encoding="utf-8")
    events = read_csv(path)
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


@pytest.mark.parametrize("name", sources.SOURCES)
def test_normalize_works_offline_on_the_recorded_payloads(name):
    events = sources.SOURCES[name].normalize(recorded_payloads(name))
    ingest.validate(events)
    assert len(events) > 0
    assert set(events["source"]) == {name}


def test_ingest_keeps_the_payloads_and_the_clean_table(store, monkeypatch):
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

    clean = clean_table(store, "eodi")
    assert len(clean) > 0
    assert clean["event_id"].is_monotonic_increasing


def test_ingest_keeps_the_payloads_when_the_mapping_fails(
    store, monkeypatch, eodi_events
):
    serve(monkeypatch, "eodi", eodi_events.assign(category="balloon"))
    with pytest.raises(ValueError, match="Unknown category"):
        ingest.ingest("eodi", store)
    assert len(store.payloads("eodi")) == 1
    assert store.backend.read("clean/eodi.parquet") is None


def test_the_shrink_guard_reads_the_clean_table(store, monkeypatch, eodi_events):
    serve(monkeypatch, "eodi", eodi_events)
    ingest.ingest("eodi", store)

    serve(monkeypatch, "eodi", eodi_events.head(9))
    with pytest.raises(ValueError, match="9 events, 10 before"):
        ingest.ingest("eodi", store)


def test_run_ingests_every_source_of_a_kind(tmp_path, monkeypatch, eodi_events):
    monkeypatch.setenv("TRACKER_STORE", "local")
    monkeypatch.setenv("TRACKER_STORE_DIR", str(tmp_path / "store"))
    serve(monkeypatch, "eodi", eodi_events)
    ingest.run("live")
    store = open_store()
    assert clean_bytes(store, "eodi") is not None
    assert clean_bytes(store, "leiden") is None


@pytest.mark.parametrize("name", sources.SOURCES)
def test_registry_matches_the_recorded_payloads(name):
    source = sources.SOURCES[name]
    assert source.kind in ("live", "static")
    events = source.normalize(recorded_payloads(name))
    assert set(events["source"]) == {name}
    ids = events["event_id"].astype(str)
    assert list(ids[~ids.str.startswith(f"{name}:")]) == []


def forbid_download(monkeypatch) -> None:
    """Make every source and reference dataset fail if its `extract` is called."""

    def fail() -> list[Payload]:
        raise AssertionError("rebuild must not download")

    for name, source in sources.SOURCES.items():
        monkeypatch.setitem(
            sources.SOURCES, name, dataclasses.replace(source, extract=fail)
        )
    for name, reference in sources.REFERENCES.items():
        monkeypatch.setitem(
            sources.REFERENCES, name, dataclasses.replace(reference, extract=fail)
        )


def serve_geonames(monkeypatch, content: bytes = b"zip") -> list[int]:
    """Make GeoNames download `content`, and count the downloads."""
    calls: list[int] = []

    def extract() -> list[Payload]:
        calls.append(1)
        return [Payload("geonames", "cities500.zip", content, "https://e/g", 200)]

    reference = dataclasses.replace(sources.REFERENCES["geonames"], extract=extract)
    monkeypatch.setitem(sources.REFERENCES, "geonames", reference)
    serve_misp(monkeypatch)
    return calls


def serve_misp(monkeypatch, content: bytes = b"{}") -> list[int]:
    """Make the MISP galaxy download `content`, and count the downloads."""
    calls: list[int] = []

    def extract() -> list[Payload]:
        calls.append(1)
        return [
            Payload("misp_galaxy", "threat-actor.json", content, "https://e/m", 200)
        ]

    reference = dataclasses.replace(sources.REFERENCES["misp_galaxy"], extract=extract)
    monkeypatch.setitem(sources.REFERENCES, "misp_galaxy", reference)
    return calls


def test_rebuild_applies_a_changed_mapping_without_a_download(store, monkeypatch):
    serve_recorded(monkeypatch, "eodi")
    ingest.ingest("eodi", store)
    before = clean_table(store, "eodi")
    assert "drone_overflight" in set(before["category"])

    forbid_download(monkeypatch)
    monkeypatch.setitem(eodi.CATEGORY, "sobrevuelo", "airspace_incursion")
    ingest.rebuild("eodi", store)

    after = clean_table(store, "eodi")
    assert "drone_overflight" not in set(after["category"])
    assert len(after) == len(before)
    assert len(store.payloads("eodi")) == 2


def test_the_rebuild_command_runs_without_sockets(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKER_STORE", "local")
    monkeypatch.setenv("TRACKER_STORE_DIR", str(tmp_path / "store"))
    serve_recorded(monkeypatch, "eodi")
    serve_recorded(monkeypatch, "leiden")
    serve_geonames(monkeypatch)
    cli.main(["ingest", "live"])
    cli.main(["ingest", "static"])
    store = open_store()
    for name in sources.SOURCES:
        (tmp_path / "store" / ingest.clean_path(name)).unlink()

    forbid_download(monkeypatch)
    cli.main(["rebuild", "eodi"])
    assert clean_bytes(store, "eodi") is not None
    assert clean_bytes(store, "leiden") is None

    cli.main(["rebuild"])
    for name in sources.SOURCES:
        assert len(clean_table(store, name)) > 0


def test_rebuild_reads_the_latest_payload_only(store, monkeypatch):
    serve_recorded(monkeypatch, "eodi")
    ingest.ingest("eodi", store)
    files = recorded_payloads("eodi")
    first = pd.read_csv(io.BytesIO(files["incidentes.csv"]))
    title = "Newer title"
    newer = first.assign(titulo_en=title).to_csv(index=False).encode()
    store.save(Payload("eodi", "incidentes.csv", newer, "https://e/all", 200))
    assert title not in set(clean_table(store, "eodi")["title"])

    forbid_download(monkeypatch)
    ingest.rebuild("eodi", store)
    assert len(store.payloads("eodi")) == 3
    clean = clean_table(store, "eodi")
    assert title in set(clean["title"])


def test_rebuild_without_a_stored_payload(store, monkeypatch, caplog):
    serve_recorded(monkeypatch, "eodi")
    ingest.ingest("eodi", store)
    caplog.set_level("INFO")

    ingest.rebuild(None, store)
    assert "leiden: no stored payload, skipped" in caplog.text
    assert clean_bytes(store, "leiden") is None

    with pytest.raises(ValueError, match="leiden: no stored payload"):
        ingest.rebuild("leiden", store)
    with pytest.raises(KeyError):
        ingest.rebuild("bogus", store)


def test_rebuild_writes_nothing_when_validate_fails(store, monkeypatch):
    serve_recorded(monkeypatch, "eodi")
    ingest.ingest("eodi", store)
    table = clean_bytes(store, "eodi")

    monkeypatch.setitem(eodi.CATEGORY, "sobrevuelo", "balloon")
    with pytest.raises(ValueError, match="Unknown category"):
        ingest.rebuild("eodi", store)
    assert clean_bytes(store, "eodi") == table


def test_rebuild_needs_the_override_to_lose_more_than_five_percent(
    store, monkeypatch, caplog
):
    serve_recorded(monkeypatch, "eodi")
    ingest.ingest("eodi", store)
    before = clean_bytes(store, "eodi")
    count = len(clean_table(store, "eodi"))

    def shrunk(files):
        return full(files).head(count - 2)

    full = sources.SOURCES["eodi"].normalize
    monkeypatch.setitem(
        sources.SOURCES,
        "eodi",
        dataclasses.replace(sources.SOURCES["eodi"], normalize=shrunk),
    )
    with pytest.raises(ValueError, match=f"{count - 2} events, {count} before"):
        ingest.rebuild("eodi", store)
    assert clean_bytes(store, "eodi") == before
    assert len(clean_table(store, "eodi")) == count

    caplog.set_level("INFO")
    ingest.rebuild("eodi", store, allow_shrink=True)
    assert len(clean_table(store, "eodi")) == count - 2
    assert "Shrink guard skipped by override" in caplog.text


def test_the_rebuild_command_takes_the_override_flag():
    args = cli.build_parser().parse_args(["rebuild", "eodi", "--allow-shrink"])
    assert (args.source, args.allow_shrink) == ("eodi", True)
    args = cli.build_parser().parse_args(["rebuild"])
    assert (args.source, args.allow_shrink) == (None, False)


def test_if_missing_writes_the_missing_clean_table_and_skips_a_stored_one(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TRACKER_STORE", "local")
    monkeypatch.setenv("TRACKER_STORE_DIR", str(tmp_path / "store"))
    serve_recorded(monkeypatch, "leiden")
    serve_geonames(monkeypatch)
    store = open_store()
    assert store.backend.read(ingest.clean_path("leiden")) is None

    cli.main(["ingest", "static", "--if-missing"])
    assert len(clean_table(store, "leiden")) > 0

    forbid_download(monkeypatch)
    cli.main(["ingest", "static", "--if-missing"])


def test_if_missing_downloads_again_when_the_clean_table_is_gone(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKER_STORE", "local")
    monkeypatch.setenv("TRACKER_STORE_DIR", str(tmp_path / "store"))
    serve_recorded(monkeypatch, "leiden")
    serve_geonames(monkeypatch)
    cli.main(["ingest", "static"])
    store = open_store()
    (tmp_path / "store" / ingest.clean_path("leiden")).unlink()

    cli.main(["ingest", "leiden", "--if-missing"])
    assert len(clean_table(store, "leiden")) > 0


def test_if_missing_gives_a_clean_table_with_no_lineage_its_version_and_record(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TRACKER_STORE", "local")
    monkeypatch.setenv("TRACKER_STORE_DIR", str(tmp_path / "store"))
    serve_recorded(monkeypatch, "leiden")
    serve_geonames(monkeypatch)
    cli.main(["ingest", "static"])
    store = open_store()
    (tmp_path / "store" / ingest.lineage_path("leiden")).unlink()
    assert ingest.read_lineage("leiden", store) is None
    files = recorded_payloads("leiden")
    files["version.json"] = (FIXTURES / "leiden_dataverse_version.json").read_bytes()
    monkeypatch.setitem(
        sources.SOURCES,
        "leiden",
        dataclasses.replace(
            sources.SOURCES["leiden"],
            extract=lambda: [
                Payload("leiden", n, c, f"https://e/{n}", 200) for n, c in files.items()
            ],
        ),
    )

    cli.main(["ingest", "static", "--if-missing"])
    assert lineage_of(store, "leiden")["updated_by_source"] == "2026-01-13T14:22:43Z"


def test_geonames_is_kept_in_the_store_with_its_digest_and_has_no_clean_table(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TRACKER_STORE", "local")
    monkeypatch.setenv("TRACKER_STORE_DIR", str(tmp_path / "store"))
    serve_geonames(monkeypatch, b"zip bytes")

    cli.main(["ingest", "geonames"])

    store = open_store()
    (line,) = store.payloads("geonames")
    assert line["sha256"] == hashlib.sha256(b"zip bytes").hexdigest()
    assert line["fetched_at"]
    assert store.latest("geonames", "cities500.zip") == b"zip bytes"
    assert store.backend.read(ingest.clean_path("geonames")) is None


def test_the_misp_galaxy_is_kept_in_the_store_and_has_no_clean_table(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TRACKER_STORE", "local")
    monkeypatch.setenv("TRACKER_STORE_DIR", str(tmp_path / "store"))
    serve_misp(monkeypatch, b'{"values": []}')

    cli.main(["ingest", "misp_galaxy"])

    store = open_store()
    (line,) = store.payloads("misp_galaxy")
    assert line["sha256"] == hashlib.sha256(b'{"values": []}').hexdigest()
    assert store.latest("misp_galaxy", "threat-actor.json") == b'{"values": []}'
    assert store.backend.read(ingest.clean_path("misp_galaxy")) is None


def test_the_misp_galaxy_is_an_open_cc0_reference_and_not_an_event_source():
    assert "misp_galaxy" not in sources.SOURCES
    reference = sources.REFERENCES["misp_galaxy"]
    assert reference.family == "open"
    assert reference.licence == "CC0 1.0"
    assert reference.licence_url.endswith("/publicdomain/zero/1.0/")


def test_geonames_is_a_reference_and_not_an_event_source():
    assert "geonames" in sources.REFERENCES
    assert "geonames" not in sources.SOURCES
    reference = sources.REFERENCES["geonames"]
    assert reference.licence == "CC BY 4.0"
    assert reference.family == "open"
    assert reference.licence_url == "https://creativecommons.org/licenses/by/4.0/"
    assert reference.citation


def test_the_static_target_downloads_geonames_again_by_hand(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKER_STORE", "local")
    monkeypatch.setenv("TRACKER_STORE_DIR", str(tmp_path / "store"))
    serve_recorded(monkeypatch, "leiden")
    calls = serve_geonames(monkeypatch)

    cli.main(["ingest", "static"])
    cli.main(["ingest", "static"])

    assert len(calls) == 2


def test_if_missing_downloads_geonames_once(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKER_STORE", "local")
    monkeypatch.setenv("TRACKER_STORE_DIR", str(tmp_path / "store"))
    serve_recorded(monkeypatch, "leiden")
    calls = serve_geonames(monkeypatch)

    cli.main(["ingest", "static", "--if-missing"])
    cli.main(["ingest", "static", "--if-missing"])

    assert len(calls) == 1
    assert len(open_store().payloads("geonames")) == 1


def test_the_live_target_does_not_download_geonames(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKER_STORE", "local")
    monkeypatch.setenv("TRACKER_STORE_DIR", str(tmp_path / "store"))
    serve_recorded(monkeypatch, "eodi")
    calls = serve_geonames(monkeypatch)

    cli.main(["ingest", "live"])

    assert calls == []


@pytest.fixture
def fixed_clock(monkeypatch):
    now = pd.Timestamp("2026-03-05 10:00:00", tz="UTC")
    monkeypatch.setattr(clock, "utc_now", lambda: now)


def lineage_of(store: Store, name: str) -> dict:
    stored = store.backend.read(ingest.lineage_path(name))
    assert stored is not None
    return json.loads(stored)


def test_the_lineage_lists_each_payload_with_its_path_and_sha256(store, monkeypatch):
    serve_recorded(monkeypatch, "eodi")
    ingest.ingest("eodi", store)

    record = lineage_of(store, "eodi")
    manifest = {line["name"]: line for line in store.manifest("eodi")}
    assert record["source"] == "eodi"
    assert record["rows"] == len(clean_table(store, "eodi"))
    assert [p["name"] for p in record["payloads"]] == sorted(manifest)
    for payload in record["payloads"]:
        line = manifest[payload["name"]]
        assert (payload["path"], payload["sha256"]) == (line["path"], line["sha256"])
        content = store.backend.read(payload["path"])
        assert hashlib.sha256(content).hexdigest() == payload["sha256"]


def test_the_lineage_points_to_the_earlier_file_of_an_unchanged_payload(
    store, monkeypatch, fixed_clock
):
    serve_recorded(monkeypatch, "eodi")
    ingest.ingest("eodi", store)
    first = lineage_of(store, "eodi")
    ingest.ingest("eodi", store)

    assert lineage_of(store, "eodi") == first
    assert [line["stored"] for line in store.manifest("eodi")] == [True] * 2 + [
        False
    ] * 2


def test_the_lineage_records_the_code_commit_without_a_new_commit_for_it(
    store, monkeypatch, fixed_clock
):
    serve_recorded(monkeypatch, "eodi")
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    ingest.ingest("eodi", store)
    assert lineage_of(store, "eodi")["code_commit"] == "a" * 40

    monkeypatch.setenv("GITHUB_SHA", "b" * 40)
    ingest.ingest("eodi", store)
    assert lineage_of(store, "eodi")["code_commit"] == "a" * 40


def test_the_lineage_is_written_again_when_a_payload_changes(store, monkeypatch):
    serve_recorded(monkeypatch, "eodi")
    ingest.ingest("eodi", store)
    first = lineage_of(store, "eodi")

    files = recorded_payloads("eodi")
    files["incidentes.csv"] += b"\n"
    source = dataclasses.replace(
        sources.SOURCES["eodi"],
        extract=lambda: [
            Payload("eodi", file, content, f"https://e/{file}", 200)
            for file, content in files.items()
        ],
    )
    monkeypatch.setitem(sources.SOURCES, "eodi", source)
    ingest.ingest("eodi", store)

    second = lineage_of(store, "eodi")
    assert second["payloads"][0]["sha256"] != first["payloads"][0]["sha256"]
    assert second["payloads"][1] == first["payloads"][1]


def test_a_rebuild_with_no_network_gives_the_leiden_release_time(store):
    version = (FIXTURES / "leiden_dataverse_version.json").read_bytes()
    files = recorded_payloads("leiden")
    files["version.json"] = version
    store.save_many(
        [Payload("leiden", n, c, f"https://e/{n}", 200) for n, c in files.items()]
    )
    ingest.rebuild("leiden", store)

    record = lineage_of(store, "leiden")
    assert record["updated_by_source"] == "2026-01-13T14:22:43Z"
    assert [p["name"] for p in record["payloads"]] == ["eventdata.xlsx", "version.json"]


def test_a_source_without_a_date_records_none(store, monkeypatch, eodi_events):
    serve(monkeypatch, "eodi", eodi_events)
    ingest.ingest("eodi", store)
    assert lineage_of(store, "eodi")["updated_by_source"] is None
