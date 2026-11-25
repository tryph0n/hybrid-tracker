"""Raw store client: keeps each source payload as fetched, with a manifest.

Layout of ``docs/decisions/0003-storage.md``: payloads under
``raw/<source>/<YYYY>/<MM>/<DD>/<file>``, and one JSON Lines manifest per
source and month at ``raw/<source>/<YYYY>/<MM>/manifest.jsonl``. Two back ends
sit behind one interface: a local folder (tests, development) and the private
Hugging Face dataset (pipeline). ``open_store`` picks one from the environment.
"""

import hashlib
import json
import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from tracker import clock

logger = logging.getLogger(__name__)

BACKEND_VAR = "TRACKER_STORE"
LOCAL_DIR_VAR = "TRACKER_STORE_DIR"
HF_REPO_VAR = "TRACKER_STORE_REPO"
DEFAULT_HF_REPO = "sam-bot/hybrid-tracker-raw"
MANIFEST_NAME = "manifest.jsonl"
MAX_FILES_PER_COMMIT = 99


class StoreError(Exception):
    """The store could not be read or written."""


class StoreQuotaError(StoreError):
    """The store refused a commit with HTTP 403: treated as a full private quota."""


class Backend(Protocol):
    def read(self, path: str) -> bytes | None:
        """Return the file at `path`, or None if it does not exist."""

    def children(self, prefix: str) -> list[str]:
        """Return the names of the entries directly under the folder `prefix`."""

    def commit(self, files: Mapping[str, bytes], message: str) -> None:
        """Write all `files` (path to bytes) together."""


@dataclass(frozen=True)
class Payload:
    source: str
    name: str
    content: bytes
    url: str
    status: int


class LocalBackend:
    def __init__(self, root: Path) -> None:
        self.root = root

    def read(self, path: str) -> bytes | None:
        target = self.root / path
        return target.read_bytes() if target.is_file() else None

    def children(self, prefix: str) -> list[str]:
        folder = self.root / prefix
        return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []

    def commit(self, files: Mapping[str, bytes], message: str) -> None:
        for path, content in files.items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)


class HfBackend:
    """Private Hugging Face dataset. `api` is an `HfApi`, or a fake in tests."""

    def __init__(self, api: Any, repo_id: str) -> None:
        self.api = api
        self.repo_id = repo_id

    def read(self, path: str) -> bytes | None:
        from huggingface_hub.errors import EntryNotFoundError

        try:
            local = self.api.hf_hub_download(self.repo_id, path, repo_type="dataset")
        except EntryNotFoundError:
            return None
        return Path(local).read_bytes()

    def children(self, prefix: str) -> list[str]:
        from huggingface_hub.errors import EntryNotFoundError

        try:
            entries = list(
                self.api.list_repo_tree(
                    self.repo_id, path_in_repo=prefix, repo_type="dataset"
                )
            )
        except EntryNotFoundError:
            return []
        return sorted(entry.path.rsplit("/", 1)[-1] for entry in entries)

    def commit(self, files: Mapping[str, bytes], message: str) -> None:
        from huggingface_hub import CommitOperationAdd
        from huggingface_hub.errors import HfHubHTTPError

        operations = [
            CommitOperationAdd(path_in_repo=path, path_or_fileobj=content)
            for path, content in files.items()
        ]
        try:
            self.api.create_commit(
                repo_id=self.repo_id,
                repo_type="dataset",
                operations=operations,
                commit_message=message,
            )
        except HfHubHTTPError as exc:
            if exc.response.status_code == 403:
                raise StoreQuotaError(
                    f"Commit to {self.repo_id} refused with HTTP 403. The private quota "
                    "(100 GB, history included) may be full: deleting files does not free it. "
                    "No retry was made."
                ) from exc
            raise


class Store:
    def __init__(self, backend: Backend) -> None:
        self.backend = backend

    def save(self, payload: Payload) -> dict[str, Any]:
        """Save one payload. Return its manifest line."""
        return self.save_many([payload])[0]

    def save_many(self, payloads: Sequence[Payload]) -> list[dict[str, Any]]:
        """Save payloads in one commit. Return one manifest line per payload.

        A payload identical to the latest stored one of the same source and file
        is not written again: only its manifest line is added, with `stored` false.
        """
        now = clock.utc_now()
        fetched_at = now.isoformat()
        manifests: dict[str, list[dict[str, Any]]] = {}
        files: dict[str, bytes] = {}
        lines: list[dict[str, Any]] = []
        for payload in payloads:
            month = f"raw/{payload.source}/{now:%Y}/{now:%m}"
            path = manifests_path(month)
            if path not in manifests:
                manifests[path] = self._read_manifest(path)
            digest = hashlib.sha256(payload.content).hexdigest()
            previous = self._latest_line(payload.source, payload.name, manifests)
            stored = previous is None or previous["sha256"] != digest
            line: dict[str, Any] = {
                "source": payload.source,
                "name": payload.name,
                "sha256": digest,
                "url": payload.url,
                "fetched_at": fetched_at,
                "status": payload.status,
                "stored": stored,
                "path": previous["path"] if previous and not stored else None,
            }
            if stored:
                line["path"] = payload_path(
                    month, now.day, payload.name, now, manifests[path]
                )
                files[line["path"]] = payload.content
            manifests[path].append(line)
            lines.append(line)
        for path, entries in manifests.items():
            files[path] = "".join(
                json.dumps(e, sort_keys=True) + "\n" for e in entries
            ).encode()
        if len(files) > MAX_FILES_PER_COMMIT:
            raise StoreError(
                f"{len(files)} files in one commit: keep it under {MAX_FILES_PER_COMMIT + 1}"
            )
        self.backend.commit(
            files, f"raw: {len(payloads)} payload(s) fetched at {fetched_at}"
        )
        logger.info(
            "Saved %d payload(s), %d file(s) written", len(payloads), len(files)
        )
        return lines

    def manifest(self, source: str) -> list[dict[str, Any]]:
        """Return all manifest lines of a source, oldest first."""
        lines: list[dict[str, Any]] = []
        for path in self._manifest_paths(source):
            lines.extend(self._read_manifest(path))
        return lines

    def payloads(self, source: str) -> list[dict[str, Any]]:
        """Return the manifest lines of the stored payloads of a source, oldest first."""
        return [line for line in self.manifest(source) if line["stored"]]

    def latest(self, source: str, name: str) -> bytes | None:
        """Return the latest stored payload of a source and file, or None."""
        line = self._latest_line(source, name, {})
        if line is None:
            return None
        content = self.backend.read(line["path"])
        if content is None:
            raise StoreError(f"Manifest points to a missing payload: {line['path']}")
        return content

    def _latest_line(
        self, source: str, name: str, pending: Mapping[str, list[dict[str, Any]]]
    ) -> dict[str, Any] | None:
        """Latest stored line of a file: pending lines of this save first, then the store."""
        for entries in reversed(list(pending.values())):
            for pending_line in reversed(entries):
                if (
                    pending_line["source"] == source
                    and pending_line["name"] == name
                    and pending_line["stored"]
                ):
                    return dict(pending_line)
        for path in reversed(self._manifest_paths(source)):
            if path in pending:
                continue
            for stored_line in reversed(self._read_manifest(path)):
                if stored_line["name"] == name and stored_line["stored"]:
                    return dict(stored_line)
        return None

    def _manifest_paths(self, source: str) -> list[str]:
        """Manifest paths of a source that exist, oldest month first."""
        paths = []
        for year in self.backend.children(f"raw/{source}"):
            if not year.isdigit():
                continue
            for month in self.backend.children(f"raw/{source}/{year}"):
                if month.isdigit():
                    paths.append(manifests_path(f"raw/{source}/{year}/{month}"))
        return sorted(paths)

    def _read_manifest(self, path: str) -> list[dict[str, Any]]:
        raw = self.backend.read(path)
        if raw is None:
            return []
        return [json.loads(row) for row in raw.decode().splitlines() if row]


def manifests_path(month_folder: str) -> str:
    return f"{month_folder}/{MANIFEST_NAME}"


def payload_path(
    month: str, day: int, name: str, now: Any, month_lines: list[dict[str, Any]]
) -> str:
    """Path of a payload. A second payload of the same file on one day gets the time in its name."""
    path = f"{month}/{day:02d}/{name}"
    if any(line["path"] == path for line in month_lines):
        stem, dot, suffix = name.rpartition(".")
        stamp = f"{now:%H%M%S}"
        timed = f"{stem}-{stamp}.{suffix}" if dot else f"{name}-{stamp}"
        path = f"{month}/{day:02d}/{timed}"
    return path


def open_store(env: Mapping[str, str] | None = None) -> Store:
    """Build the store chosen by `TRACKER_STORE`: `local` or `hf`."""
    env = os.environ if env is None else env
    kind = env.get(BACKEND_VAR)
    if kind == "local":
        folder = env.get(LOCAL_DIR_VAR)
        if not folder:
            raise StoreError(f"{LOCAL_DIR_VAR} must name the folder of the local store")
        return Store(LocalBackend(Path(folder)))
    if kind == "hf":
        from huggingface_hub import HfApi

        return Store(HfBackend(HfApi(), env.get(HF_REPO_VAR, DEFAULT_HF_REPO)))
    raise StoreError(f"{BACKEND_VAR} must be 'local' or 'hf', got {kind!r}")
