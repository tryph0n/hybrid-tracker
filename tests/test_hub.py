"""The loader of the published tables: Hub read, snapshot fallback, revision."""

import datetime
import json
from types import SimpleNamespace

import pandas as pd
import pytest

from tracker import hub
from tracker.errors import UserError

pytestmark = pytest.mark.disable_socket

SHA = "4a22c978a3094102a3487dab5cdbdb4c52d6600d"


def refuse(revision):
    raise ConnectionError("no network")


@pytest.fixture(autouse=True)
def no_quality(monkeypatch):
    """No test reads the quality table from the Hub unless it sets one."""
    monkeypatch.setattr(hub, "fetch_quality", refuse)


@pytest.fixture
def snapshot(tmp_path):
    folder = tmp_path / "snapshot"
    folder.mkdir()
    pd.DataFrame({"event_id": ["old:1"]}).to_parquet(folder / hub.EVENTS_FILE)
    (folder / hub.SNAPSHOT_INFO).write_text(
        json.dumps({"revision": "a" * 40, "date": "2026-11-20"}), encoding="utf-8"
    )
    return folder


def test_a_good_read_gives_the_revision_and_the_date(tmp_path, monkeypatch, snapshot):
    path = tmp_path / "events.parquet"
    pd.DataFrame({"event_id": ["new:1", "new:2"]}).to_parquet(path)
    monkeypatch.setattr(hub, "fetch", lambda revision: (path, SHA, "2026-11-26"))
    published = hub.load_published("main", snapshot)
    assert published.events["event_id"].tolist() == ["new:1", "new:2"]
    assert (published.revision, published.date) == (SHA, "2026-11-26")
    assert not published.from_snapshot


def test_a_failed_read_gives_the_snapshot(monkeypatch, snapshot, caplog):
    monkeypatch.setattr(hub, "fetch", refuse)
    published = hub.load_published("main", snapshot)
    assert published.events["event_id"].tolist() == ["old:1"]
    assert (published.revision, published.date) == ("a" * 40, "2026-11-20")
    assert published.from_snapshot
    assert published.failure == hub.UNREACHABLE
    assert "Could not read" in caplog.text


class RevisionNotFound(Exception):
    """Stands for the Hub answer "Invalid rev id"."""


def test_an_unknown_revision_is_not_a_connection_failure(monkeypatch, snapshot):
    def unknown(revision):
        raise RevisionNotFound("Invalid rev id: nosuchtag")

    monkeypatch.setattr(hub, "fetch", unknown)
    published = hub.load_published("nosuchtag", snapshot)
    assert published.from_snapshot
    assert published.failure == hub.UNREADABLE


def test_an_unreadable_file_is_not_a_connection_failure(
    tmp_path, monkeypatch, snapshot
):
    path = tmp_path / "events.parquet"
    path.write_text("not a parquet file", encoding="utf-8")
    monkeypatch.setattr(hub, "fetch", lambda revision: (path, SHA, "2026-11-26"))
    published = hub.load_published("main", snapshot)
    assert published.from_snapshot
    assert published.failure == hub.UNREADABLE


@pytest.mark.parametrize("name", sorted(hub.CONNECTION_ERROR_NAMES))
def test_the_client_connection_errors_are_recognised(name):
    error = type(name, (Exception,), {})("no answer")
    assert hub.is_connection_failure(error)
    assert hub.is_connection_failure(TimeoutError())


def test_a_good_read_has_no_failure(tmp_path, monkeypatch, snapshot):
    path = tmp_path / "events.parquet"
    pd.DataFrame({"event_id": ["new:1"]}).to_parquet(path)
    monkeypatch.setattr(hub, "fetch", lambda revision: (path, SHA, "2026-11-26"))
    assert hub.load_published("main", snapshot).failure == ""


def test_a_snapshot_with_no_info_file_has_an_unknown_revision(snapshot):
    (snapshot / hub.SNAPSHOT_INFO).unlink()
    published = hub.read_snapshot(snapshot)
    assert (published.revision, published.date) == ("unknown", "")


def test_no_snapshot_and_no_hub_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(hub, "fetch", refuse)
    with pytest.raises(FileNotFoundError):
        hub.load_published("main", tmp_path)


def test_fetch_downloads_the_file_at_the_resolved_commit(monkeypatch, tmp_path):
    calls = {}

    class FakeApi:
        def list_repo_commits(self, repo_id, repo_type, revision):
            calls["listed"] = (repo_id, repo_type, revision)
            when = datetime.datetime(2026, 11, 26, 23, 59, tzinfo=datetime.UTC)
            return [SimpleNamespace(commit_id=SHA, created_at=when)]

    def download(repo_id, filename, repo_type, revision):
        calls["downloaded"] = (repo_id, filename, repo_type, revision)
        return str(tmp_path / filename)

    monkeypatch.setattr("huggingface_hub.HfApi", FakeApi)
    monkeypatch.setattr("huggingface_hub.hf_hub_download", download)
    path, commit, committed = hub.fetch("v2.1.0")
    assert calls["listed"] == (hub.REPO_ID, "dataset", "v2.1.0")
    assert calls["downloaded"] == (hub.REPO_ID, "events.parquet", "dataset", SHA)
    assert (path.name, commit, committed) == ("events.parquet", SHA, "2026-11-26")


def test_the_revision_is_main_unless_the_variable_is_set(monkeypatch):
    monkeypatch.delenv(hub.REVISION_VARIABLE, raising=False)
    assert hub.configured_revision() == "main"
    monkeypatch.setenv(hub.REVISION_VARIABLE, "v2.1.0")
    assert hub.configured_revision() == "v2.1.0"
    monkeypatch.setenv(hub.REVISION_VARIABLE, "")
    assert hub.configured_revision() == "main"


def test_the_snapshot_folder_follows_its_variable(monkeypatch, tmp_path):
    monkeypatch.delenv(hub.SNAPSHOT_VARIABLE, raising=False)
    assert hub.snapshot_dir() == hub.SNAPSHOT_DIR
    monkeypatch.setenv(hub.SNAPSHOT_VARIABLE, str(tmp_path))
    assert hub.snapshot_dir() == tmp_path


def test_a_good_read_carries_the_quality_table(tmp_path, monkeypatch, snapshot):
    path = tmp_path / "events.parquet"
    pd.DataFrame({"event_id": ["new:1"]}).to_parquet(path)
    quality = pd.DataFrame({"source": ["eodi"]})
    monkeypatch.setattr(hub, "fetch", lambda revision: (path, SHA, "2026-11-26"))
    monkeypatch.setattr(hub, "fetch_quality", lambda commit: quality)
    assert hub.load_published("main", snapshot).quality is quality


def test_a_missing_quality_table_does_not_fail_the_read(
    tmp_path, monkeypatch, snapshot
):
    path = tmp_path / "events.parquet"
    pd.DataFrame({"event_id": ["new:1"]}).to_parquet(path)
    monkeypatch.setattr(hub, "fetch", lambda revision: (path, SHA, "2026-11-26"))
    monkeypatch.setattr(hub, "fetch_quality", refuse)
    published = hub.load_published("main", snapshot)
    assert published.quality is None
    assert not published.from_snapshot


def write_month(path, days):
    pd.DataFrame({"day": days, "h3_index": ["840000fffffffff"] * len(days)}).to_parquet(
        path, index=False
    )


def test_the_first_and_newest_day_come_from_the_statistics_of_the_end_files(tmp_path):
    old, new = tmp_path / "old.parquet", tmp_path / "new.parquet"
    write_month(old, [datetime.date(2026, 10, 31), datetime.date(2026, 10, 30)])
    write_month(new, [datetime.date(2026, 11, 2), datetime.date(2026, 11, 3)])

    first, last = hub.day_bounds({"t/2026-11.parquet": new, "t/2026-10.parquet": old})

    assert (first, last) == (datetime.date(2026, 10, 30), datetime.date(2026, 11, 3))


def test_a_month_file_with_no_row_has_no_bounds(tmp_path):
    path = tmp_path / "empty.parquet"
    write_month(path, [])

    with pytest.raises(UserError, match="no statistics for day"):
        hub.day_bounds({"t/2026-10.parquet": path})


def test_fetch_month_ends_downloads_the_oldest_and_the_newest_month_file(monkeypatch):
    names = [
        "events.parquet",
        "t/2026-11.parquet",
        "t/2026-09.parquet",
        "t/2026-10.parquet",
        "t/notes.parquet",
        "other/2026-01.parquet",
    ]
    downloaded = []

    class FakeApi:
        def list_repo_files(self, repo_id, repo_type, revision):
            assert (repo_id, repo_type, revision) == (hub.REPO_ID, "dataset", SHA)
            return names

    def download(repo_id, filename, repo_type, revision):
        downloaded.append((filename, revision))
        return f"/cache/{filename}"

    monkeypatch.setattr("huggingface_hub.HfApi", FakeApi)
    monkeypatch.setattr("huggingface_hub.hf_hub_download", download)

    files = hub.fetch_month_ends(SHA, "t")

    assert sorted(files) == ["t/2026-09.parquet", "t/2026-11.parquet"]
    assert sorted(downloaded) == [
        ("t/2026-09.parquet", SHA),
        ("t/2026-11.parquet", SHA),
    ]
