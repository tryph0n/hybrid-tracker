"""The raw store client: local back end, and the Hugging Face back end with a fake API."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from huggingface_hub import CommitOperationAdd
from huggingface_hub.errors import EntryNotFoundError, HfHubHTTPError

from tracker import clock, store
from tracker.store import (
    HfBackend,
    LocalBackend,
    Payload,
    Store,
    StoreError,
    StoreQuotaError,
    open_store,
)

pytestmark = pytest.mark.disable_socket

URL = "https://example.org/register.csv"


def payload(
    content: bytes = b"a,b\n1,2\n", source: str = "eodi", name: str = "register.csv"
):
    return Payload(source=source, name=name, content=content, url=URL, status=200)


def set_clock(monkeypatch, stamp: str) -> None:
    now = pd.Timestamp(stamp, tz="UTC")
    monkeypatch.setattr(clock, "utc_now", lambda: now)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    set_clock(monkeypatch, "2026-03-05 10:00:00")


@pytest.fixture
def local(tmp_path):
    return Store(LocalBackend(tmp_path))


def manifest_lines(
    root: Path, source: str = "eodi", month: str = "2026/03"
) -> list[dict]:
    text = (root / "raw" / source / month / "manifest.jsonl").read_text()
    return [json.loads(row) for row in text.splitlines()]


def test_save_writes_payload_and_manifest_line(local, tmp_path):
    line = local.save(payload())
    assert (tmp_path / "raw/eodi/2026/03/05/register.csv").read_bytes() == b"a,b\n1,2\n"
    (written,) = manifest_lines(tmp_path)
    assert written == line
    assert written["source"] == "eodi"
    assert written["name"] == "register.csv"
    assert written["sha256"] == hashlib.sha256(b"a,b\n1,2\n").hexdigest()
    assert written["url"] == URL
    assert written["fetched_at"] == "2026-03-05T10:00:00+00:00"
    assert written["status"] == 200
    assert written["stored"] is True


def test_same_bytes_write_no_payload_but_a_check_line(local, tmp_path, monkeypatch):
    local.save(payload())
    set_clock(monkeypatch, "2026-03-06 10:00:00")
    line = local.save(payload())
    assert not (tmp_path / "raw/eodi/2026/03/06").exists()
    first, check = manifest_lines(tmp_path)
    assert check == line
    assert check["stored"] is False
    assert check["sha256"] == first["sha256"]
    assert check["fetched_at"] == "2026-03-06T10:00:00+00:00"
    assert check["path"] == first["path"]


def test_changed_bytes_are_stored_again_and_latest_is_the_new_one(local, monkeypatch):
    local.save(payload(b"v1"))
    set_clock(monkeypatch, "2026-03-06 10:00:00")
    local.save(payload(b"v2"))
    assert local.latest("eodi", "register.csv") == b"v2"
    assert [line["stored"] for line in local.manifest("eodi")] == [True, True]


def test_reverting_to_older_bytes_is_stored_again(local, monkeypatch):
    local.save(payload(b"v1"))
    set_clock(monkeypatch, "2026-03-06 10:00:00")
    local.save(payload(b"v2"))
    set_clock(monkeypatch, "2026-03-07 10:00:00")
    local.save(payload(b"v1"))
    assert len(local.payloads("eodi")) == 3


def test_two_changes_on_one_day_keep_both_payloads(local, tmp_path, monkeypatch):
    local.save(payload(b"v1"))
    set_clock(monkeypatch, "2026-03-05 15:30:45")
    local.save(payload(b"v2"))
    assert (tmp_path / "raw/eodi/2026/03/05/register.csv").read_bytes() == b"v1"
    assert (tmp_path / "raw/eodi/2026/03/05/register-153045.csv").read_bytes() == b"v2"
    assert local.latest("eodi", "register.csv") == b"v2"


def test_latest_is_none_for_an_unknown_source(local):
    assert local.latest("eodi", "register.csv") is None
    assert local.manifest("eodi") == []


def test_latest_looks_across_months_and_is_per_file(local, monkeypatch):
    local.save(payload(b"old"))
    local.save(payload(b"other", name="other.json"))
    set_clock(monkeypatch, "2026-05-01 00:00:00")
    local.save(payload(b"old"))
    assert local.latest("eodi", "register.csv") == b"old"
    assert local.latest("eodi", "other.json") == b"other"
    assert len(local.manifest("eodi")) == 3


def test_manifest_is_one_file_per_source_and_month(local, tmp_path, monkeypatch):
    local.save(payload())
    local.save(payload(source="leiden", name="events.json"))
    set_clock(monkeypatch, "2026-04-01 00:00:00")
    local.save(payload(b"new"))
    assert (tmp_path / "raw/eodi/2026/03/manifest.jsonl").is_file()
    assert (tmp_path / "raw/eodi/2026/04/manifest.jsonl").is_file()
    assert (tmp_path / "raw/leiden/2026/03/manifest.jsonl").is_file()
    assert [line["fetched_at"][:7] for line in local.manifest("eodi")] == [
        "2026-03",
        "2026-04",
    ]


def test_save_many_dedups_inside_one_group(local):
    lines = local.save_many([payload(b"x"), payload(b"x"), payload(b"y", name="b.csv")])
    assert [line["stored"] for line in lines] == [True, False, True]


def test_too_many_files_in_one_commit_is_refused(local):
    many = [payload(bytes([i]), name=f"f{i}.bin") for i in range(100)]
    with pytest.raises(StoreError, match="keep it under"):
        local.save_many(many)
    assert local.manifest("eodi") == []


def test_open_store_from_environment(tmp_path):
    chosen = open_store({"TRACKER_STORE": "local", "TRACKER_STORE_DIR": str(tmp_path)})
    assert isinstance(chosen.backend, LocalBackend)
    assert chosen.backend.root == tmp_path


@pytest.mark.parametrize(
    "env",
    [{}, {"TRACKER_STORE": "s3"}, {"TRACKER_STORE": "local"}],
)
def test_open_store_rejects_a_bad_environment(env):
    with pytest.raises(StoreError):
        open_store(env)


def test_open_store_hf_uses_the_default_repo():
    chosen = open_store({"TRACKER_STORE": "hf"})
    assert isinstance(chosen.backend, HfBackend)
    assert chosen.backend.repo_id == store.DEFAULT_HF_REPO
    other = open_store({"TRACKER_STORE": "hf", "TRACKER_STORE_REPO": "me/other"})
    assert other.backend.repo_id == "me/other"


class FakeHfApi:
    """In-memory stand-in for HfApi: files by path, and a log of commits."""

    def __init__(self, tmp_path: Path, error: Exception | None = None) -> None:
        self.tmp_path = tmp_path
        self.files: dict[str, bytes] = {}
        self.commits: list[dict] = []
        self.error = error

    def hf_hub_download(self, repo_id, filename, *, repo_type):
        assert repo_type == "dataset"
        if filename not in self.files:
            raise EntryNotFoundError(filename)
        target = self.tmp_path / "dl" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.files[filename])
        return str(target)

    def list_repo_tree(self, repo_id, path_in_repo=None, *, repo_type):
        assert repo_type == "dataset"
        prefix = f"{path_in_repo}/"
        names = {
            p[len(prefix) :].split("/")[0] for p in self.files if p.startswith(prefix)
        }
        if not names:
            raise EntryNotFoundError(path_in_repo)
        return [SimpleNamespace(path=f"{prefix}{name}") for name in sorted(names)]

    def create_commit(self, *, repo_id, repo_type, operations, commit_message):
        self.commits.append(
            {"repo_id": repo_id, "repo_type": repo_type, "ops": operations}
        )
        if self.error:
            raise self.error
        for op in operations:
            assert isinstance(op, CommitOperationAdd)
            self.files[op.path_in_repo] = op.path_or_fileobj


@pytest.fixture
def fake(tmp_path):
    return FakeHfApi(tmp_path)


def hf_store(api) -> Store:
    return Store(HfBackend(api, "me/raw"))


def test_hf_save_makes_one_commit_with_payload_and_manifest(fake):
    hf_store(fake).save(payload())
    (commit,) = fake.commits
    assert commit["repo_id"] == "me/raw"
    assert commit["repo_type"] == "dataset"
    assert sorted(op.path_in_repo for op in commit["ops"]) == [
        "raw/eodi/2026/03/05/register.csv",
        "raw/eodi/2026/03/manifest.jsonl",
    ]
    manifest = fake.files["raw/eodi/2026/03/manifest.jsonl"].decode().splitlines()
    assert len(manifest) == 1
    assert (
        json.loads(manifest[0])["sha256"] == hashlib.sha256(b"a,b\n1,2\n").hexdigest()
    )


def test_hf_group_of_saves_makes_one_commit(fake):
    hf_store(fake).save_many(
        [payload(b"1"), payload(b"2", name="b.csv"), payload(b"3", source="x")]
    )
    (commit,) = fake.commits
    assert len(commit["ops"]) == 5
    assert len(fake.files["raw/eodi/2026/03/manifest.jsonl"].decode().splitlines()) == 2


def test_hf_reads_back_across_runs(fake, monkeypatch):
    hf_store(fake).save(payload(b"v1"))
    set_clock(monkeypatch, "2026-04-02 10:00:00")
    second = hf_store(fake)
    second.save(payload(b"v1"))
    assert [line["stored"] for line in second.manifest("eodi")] == [True, False]
    assert second.latest("eodi", "register.csv") == b"v1"
    assert hf_store(fake).latest("none", "register.csv") is None
    assert len(fake.commits) == 2


def http_error(status: int) -> HfHubHTTPError:
    response = SimpleNamespace(status_code=status, headers={}, request=None)
    return HfHubHTTPError(f"HTTP {status}", response=response)


def test_hf_403_on_commit_raises_and_is_not_retried(tmp_path):
    api = FakeHfApi(tmp_path, error=http_error(403))
    with pytest.raises(StoreQuotaError, match="quota"):
        hf_store(api).save(payload())
    assert len(api.commits) == 1


def test_hf_other_http_errors_pass_through(tmp_path):
    api = FakeHfApi(tmp_path, error=http_error(500))
    with pytest.raises(HfHubHTTPError):
        hf_store(api).save(payload())
    assert len(api.commits) == 1


def test_latest_fails_when_the_manifest_points_to_a_missing_payload(local, tmp_path):
    line = local.save(payload())
    (tmp_path / line["path"]).unlink()
    with pytest.raises(StoreError, match="missing payload"):
        local.latest("eodi", "register.csv")


def test_two_changes_in_the_same_second_keep_every_payload(
    local, tmp_path, monkeypatch
):
    local.save(payload(b"v1"))
    set_clock(monkeypatch, "2026-03-05 15:30:45")
    local.save(payload(b"v2"))
    local.save(payload(b"v3"))
    day = tmp_path / "raw/eodi/2026/03/05"
    assert (day / "register.csv").read_bytes() == b"v1"
    assert (day / "register-153045.csv").read_bytes() == b"v2"
    assert (day / "register-153045-2.csv").read_bytes() == b"v3"
    assert local.latest("eodi", "register.csv") == b"v3"


def test_same_second_changes_in_one_group_keep_every_payload(
    local, tmp_path, monkeypatch
):
    local.save(payload(b"v1"))
    set_clock(monkeypatch, "2026-03-05 15:30:45")
    local.save_many([payload(b"v2"), payload(b"v3")])
    assert len(local.payloads("eodi")) == 3
    assert len({line["path"] for line in local.payloads("eodi")}) == 3


@pytest.mark.parametrize(
    "bad", ["", "..", ".", "a/b", "../x", "/abs", ".hidden", "a b", "a\\b"]
)
def test_save_rejects_an_unsafe_source(local, tmp_path, bad):
    with pytest.raises(StoreError):
        local.save(payload(source=bad))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("bad", ["", "..", "a/b", "../x", "/abs", ".hidden", "a b"])
def test_save_rejects_an_unsafe_name(local, tmp_path, bad):
    with pytest.raises(StoreError):
        local.save(payload(name=bad))
    assert list(tmp_path.iterdir()) == []
