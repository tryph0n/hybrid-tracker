"""The ingest command with fetch functions replaced by sample frames."""

import dataclasses

import pandas as pd
import pytest
from conftest import FIXTURES

from tracker import ingest, sources
from tracker.sources import read_events

HEADER = (
    "event_id,source,date,date_precision,countries,place,lat,lon,category,"
    "subcategory,title,event_status,russia_link,url\n"
)


def serve(monkeypatch, name: str, events: pd.DataFrame) -> None:
    """Make source `name` fetch `events` instead of downloading."""
    source = dataclasses.replace(sources.SOURCES[name], fetch=lambda: events)
    monkeypatch.setitem(sources.SOURCES, name, source)


@pytest.fixture
def eodi_events() -> pd.DataFrame:
    return pd.read_csv(
        FIXTURES / "events" / "eodi.csv", keep_default_na=False, na_values=[""]
    )


def test_ingest_stores_a_sorted_deterministic_file(data_dir, monkeypatch, eodi_events):
    serve(monkeypatch, "eodi", eodi_events.sample(frac=1, random_state=1))
    ingest.ingest("eodi")
    first = (data_dir / "eodi.csv").read_bytes()

    stored = read_events("eodi")
    assert stored["event_id"].is_monotonic_increasing
    expected = eodi_events.sort_values("event_id", ignore_index=True)
    pd.testing.assert_frame_equal(stored, expected)

    serve(monkeypatch, "eodi", eodi_events.sample(frac=1, random_state=2))
    ingest.ingest("eodi")
    assert (data_dir / "eodi.csv").read_bytes() == first


def test_ingest_rejects_a_shrunken_source(data_dir, monkeypatch, eodi_events):
    serve(monkeypatch, "eodi", eodi_events)
    ingest.ingest("eodi")
    before = (data_dir / "eodi.csv").read_bytes()

    serve(monkeypatch, "eodi", eodi_events.head(9))
    with pytest.raises(ValueError, match="9 events, 10 before"):
        ingest.ingest("eodi")
    assert (data_dir / "eodi.csv").read_bytes() == before


def test_ingest_rejects_invalid_events(data_dir, monkeypatch, eodi_events):
    broken = eodi_events.assign(category="balloon")
    serve(monkeypatch, "eodi", broken)
    with pytest.raises(ValueError, match="Unknown category"):
        ingest.ingest("eodi")
    assert not (data_dir / "eodi.csv").exists()


def test_check_rejects_an_id_shared_by_two_sources(sample_data_dir, capsys):
    ingest.check()
    assert capsys.readouterr().out.strip().endswith("2 sources: valid")

    eodi_text = (sample_data_dir / "eodi.csv").read_text(encoding="utf-8")
    (sample_data_dir / "leiden.csv").write_text(eodi_text, encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate event_id"):
        ingest.check()


def test_read_events_keeps_the_namibia_code(data_dir):
    row = "eodi:X,eodi,2025-01-01,day,NA,Windhoek,,,other,x,Title,confirmed,none,https://e\n"
    (data_dir / "eodi.csv").write_text(HEADER + row, encoding="utf-8")
    events = read_events("eodi")
    assert events.loc[0, "countries"] == "NA"
    assert pd.isna(events.loc[0, "lat"])


@pytest.mark.parametrize("target", ["", "bogus"])
def test_main_rejects_an_unknown_target(target):
    with pytest.raises(SystemExit) as stop:
        ingest.main(target)
    assert stop.value.code == ingest.__doc__
