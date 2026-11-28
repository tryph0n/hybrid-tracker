"""The Stanford source in the registry: ingest, rebuild and `--if-missing`."""

from datetime import date

import pandas as pd
import pytest
from test_stanford import FIXTURE, fetcher, month_table

from tracker import clock, ingest, stanford
from tracker.errors import UserError
from tracker.sources import SOURCES
from tracker.store import LocalBackend, Store

pytestmark = pytest.mark.disable_socket

TODAY = date(2026, 11, 24)
MONTH_FILE = "clean/stanford/2026-11.parquet"


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    now = pd.Timestamp("2026-11-24 04:17:00", tz="UTC")
    monkeypatch.setattr(clock, "utc_now", lambda: now)


@pytest.fixture
def local(tmp_path):
    return Store(LocalBackend(tmp_path))


def serve(monkeypatch) -> list[list]:
    """Make the fetch serve the fixture for each day asked, and record the days."""
    asked: list[list] = []

    def fetch_days(days, *args, **kwargs):
        asked.append(list(days))
        return fetcher({day: FIXTURE for day in days})(days)

    monkeypatch.setattr(stanford, "fetch_days", fetch_days)
    return asked


def test_the_registry_entry_records_the_licence_the_family_and_the_credit_line():
    source = SOURCES[stanford.SOURCE]

    assert source.licence == "CC BY-NC 4.0"
    assert source.family == "nc"
    assert source.table == "context"
    assert source.licence_page == "https://rfi.stanford.edu/resources"
    assert source.citation == "Source: Stanford GPS Lab, rfi.stanford.edu."


def test_an_ingest_writes_the_month_files_and_the_record_and_no_event_table(
    local, monkeypatch
):
    serve(monkeypatch)

    ingest.ingest(stanford.SOURCE, local)

    assert set(month_table(local, "2026-11")["day"]) == {TODAY}
    record = ingest.read_lineage(stanford.SOURCE, local)
    assert [p["name"] for p in record["payloads"]] == ["heatmap-2026-11-24.json.br"]
    assert local.backend.read(ingest.clean_path(stanford.SOURCE)) is None


def test_a_rebuild_writes_the_month_files_again_and_keeps_the_record(
    local, monkeypatch
):
    serve(monkeypatch)
    ingest.ingest(stanford.SOURCE, local)
    record = local.backend.read(ingest.lineage_path(stanford.SOURCE))
    month = local.backend.read(MONTH_FILE)
    (local.backend.root / MONTH_FILE).unlink()

    ingest.rebuild(stanford.SOURCE, local)
    ingest.rebuild(None, local)

    assert local.backend.read(MONTH_FILE) == month
    assert local.backend.read(ingest.lineage_path(stanford.SOURCE)) == record
    assert local.backend.read(ingest.clean_path(stanford.SOURCE)) is None


def test_a_rebuild_of_the_source_with_no_stored_day_fails(local):
    with pytest.raises(UserError, match="stanford: no stored payload"):
        ingest.rebuild(stanford.SOURCE, local)


def test_if_missing_finds_the_month_files_and_skips_the_fetch(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACKER_STORE", "local")
    monkeypatch.setenv("TRACKER_STORE_DIR", str(tmp_path / "store"))
    asked = serve(monkeypatch)
    ingest.run(stanford.SOURCE)
    assert len(asked) == 1

    ingest.run(stanford.SOURCE, if_missing=True)
    assert len(asked) == 1

    (tmp_path / "store" / MONTH_FILE).unlink()
    ingest.run(stanford.SOURCE, if_missing=True)
    assert len(asked) == 2
    assert (tmp_path / "store" / MONTH_FILE).exists()
