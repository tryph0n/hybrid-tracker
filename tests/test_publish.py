"""Publish to the public datasets: one commit, read-back and tags, with a fake HfApi."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from huggingface_hub import CommitOperationAdd, CommitOperationDelete
from huggingface_hub.errors import HfHubHTTPError

from tracker import publish
from tracker.errors import UserError

pytestmark = pytest.mark.disable_socket

OPEN = publish.DATASETS["open"]
NC = publish.DATASETS["nc"]
VERSION = f"v{publish.SCHEMA_VERSION}"


def http_error(status: int) -> HfHubHTTPError:
    response = SimpleNamespace(status_code=status, headers={}, request=None)
    return HfHubHTTPError(f"HTTP {status}", response=response)


class FakeHfApi:
    """In-memory Hub: files of `main`, one snapshot per commit, and tags.

    `delete_tag` fails the test, so a run that calls it is caught.
    """

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.main: dict[str, dict[str, bytes]] = {}
        self.snapshots: dict[str, dict[str, bytes]] = {}
        self.tags: dict[str, dict[str, str]] = {}
        self.tag_calls: list[tuple[str, str, str]] = []
        self.tag_error: Exception | None = None
        self.corrupt: str | None = None
        self.skip: str | None = None

    def list_repo_files(self, repo_id, *, repo_type, revision=None):
        assert repo_type == "dataset"
        files = self.snapshots[revision] if revision else self.main.get(repo_id, {})
        return sorted(files)

    def create_commit(self, *, repo_id, repo_type, operations, commit_message):
        assert repo_type == "dataset"
        files = dict(self.main.get(repo_id, {}))
        for op in operations:
            if isinstance(op, CommitOperationAdd):
                if op.path_in_repo != self.skip:
                    files[op.path_in_repo] = (
                        b"corrupt"
                        if op.path_in_repo == self.corrupt
                        else op.path_or_fileobj
                    )
            else:
                assert isinstance(op, CommitOperationDelete)
                del files[op.path_in_repo]
        oid = f"{repo_id}@{len(self.snapshots) + 1}"
        self.main[repo_id] = files
        self.snapshots[oid] = files
        return SimpleNamespace(oid=oid)

    def hf_hub_download(self, repo_id, filename, *, revision, repo_type, cache_dir):
        assert repo_type == "dataset"
        target = Path(cache_dir) / filename
        target.write_bytes(self.snapshots[revision][filename])
        return str(target)

    def list_repo_refs(self, repo_id, *, repo_type):
        assert repo_type == "dataset"
        names = self.tags.get(repo_id, {})
        return SimpleNamespace(tags=[SimpleNamespace(name=name) for name in names])

    def create_tag(self, repo_id, *, tag, revision, repo_type, exist_ok):
        assert repo_type == "dataset"
        assert exist_ok is False
        self.tag_calls.append((repo_id, tag, revision))
        if self.tag_error:
            raise self.tag_error
        self.tags.setdefault(repo_id, {})[tag] = revision

    def delete_tag(self, *args, **kwargs):
        raise AssertionError("delete_tag must never be called")


@pytest.fixture
def api(tmp_path):
    return FakeHfApi(tmp_path)


@pytest.fixture
def build(tmp_path):
    folder = tmp_path / "build"
    for family in ("open", "nc"):
        (folder / family).mkdir(parents=True)
        for name in ("events.parquet", "events.csv", "sources.parquet"):
            (folder / family / name).write_bytes(f"{family} {name}".encode())
    return folder


def publish_both(api, build):
    for family in ("open", "nc"):
        publish.run(family, build, api)


def test_a_new_schema_version_creates_one_tag_per_dataset(api, build):
    publish_both(api, build)

    assert [(repo, tag) for repo, tag, _ in api.tag_calls] == [
        (OPEN, VERSION),
        (NC, VERSION),
    ]
    for repo in (OPEN, NC):
        assert api.tags[repo][VERSION] in api.snapshots


def test_a_tag_is_on_the_commit_of_the_upload(api, build):
    publish.run("open", build, api)

    assert api.snapshots[api.tags[OPEN][VERSION]] == api.main[OPEN]


def test_the_same_schema_version_creates_no_tag_and_moves_none(api, build):
    publish_both(api, build)
    tags = {repo: dict(tags) for repo, tags in api.tags.items()}
    api.tag_calls.clear()
    (build / "open" / "events.parquet").write_bytes(b"newer rows")

    publish_both(api, build)

    assert api.tag_calls == []
    assert api.tags == tags
    assert api.main[OPEN]["events.parquet"] == b"newer rows"


def test_a_refused_tag_warns_and_writes_the_tag_and_the_commit(
    api, build, tmp_path, monkeypatch, caplog
):
    api.tag_error = http_error(403)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    publish_both(api, build)

    assert api.tags == {}
    text = summary.read_text(encoding="utf-8")
    for repo in (OPEN, NC):
        assert f"Could not create the tag {VERSION} on {repo}" in text
    assert "read-back passed" in text
    assert any(record.levelname == "WARNING" for record in caplog.records)
    oids = [oid for _, _, oid in api.tag_calls]
    assert all(oid in text for oid in oids)


def test_a_failed_tag_list_warns_and_creates_nothing(api, build, tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise http_error(500)

    api.list_repo_refs = refuse
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    publish.run("open", build, api)

    assert api.tag_calls == []
    assert "Could not list the tags" in summary.read_text(encoding="utf-8")


def test_the_first_open_publish_removes_the_legacy_csv_files(api, build):
    api.main[OPEN] = {
        "eodi.csv": b"old",
        "leiden.csv": b"old",
        ".gitattributes": b"attributes",
    }
    api.snapshots["old-v2"] = dict(api.main[OPEN])

    publish.run("open", build, api)

    assert sorted(api.main[OPEN]) == [
        ".gitattributes",
        "README.md",
        "events.csv",
        "events.parquet",
        "sources.parquet",
    ]
    assert api.snapshots["old-v2"]["eodi.csv"] == b"old"


def test_a_second_publish_has_no_delete(api, build):
    publish.run("open", build, api)
    deleted = []
    create = api.create_commit

    def spy(**kwargs):
        deleted.extend(
            op for op in kwargs["operations"] if isinstance(op, CommitOperationDelete)
        )
        return create(**kwargs)

    api.create_commit = spy
    publish.run("open", build, api)

    assert deleted == []


def test_the_nc_dataset_gets_no_legacy_delete(api, build):
    publish.run("nc", build, api)

    assert "README.md" in api.main[NC]


def test_the_read_back_fails_on_a_file_that_differs_on_the_hub(api, build):
    api.corrupt = "events.parquet"

    with pytest.raises(UserError, match="events.parquet differs"):
        publish.run("open", build, api)


def test_the_read_back_fails_on_a_file_that_the_upload_skipped(api, build):
    api.skip = "sources.parquet"

    with pytest.raises(UserError, match="sources.parquet is missing"):
        publish.run("open", build, api)


def test_a_failed_read_back_creates_no_tag(api, build):
    api.corrupt = "events.parquet"

    with pytest.raises(UserError):
        publish.run("open", build, api)

    assert api.tag_calls == []


def test_the_card_is_part_of_the_commit_and_read_back(api, build):
    publish.run("open", build, api)

    card = api.main[OPEN]["README.md"].decode()
    assert "## Versions" in card
    assert card.startswith("---\nlicense: cc-by-4.0\n")


def test_a_build_folder_without_the_events_table_stops(api, build):
    (build / "nc" / "events.parquet").unlink()

    with pytest.raises(UserError, match="events.parquet"):
        publish.run("nc", build, api)

    assert api.main == {}


def test_an_unknown_family_stops(api, build):
    with pytest.raises(UserError, match="Unknown family"):
        publish.run("other", build, api)
