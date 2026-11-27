"""Read the published tables from the public Hugging Face dataset.

The dashboard reads `events.parquet` of the NonCommercial dataset, which holds
the rows of both licence families, and its `quality.parquet` for the dates of
each source (blank when that file cannot be read). When the Hub cannot be reached, it reads a
snapshot of the same file that the image build bundles in a folder, with the
revision and the date of that snapshot in `snapshot.json`.

This module has no Streamlit code: the app adds the cache.
"""

import json
import logging
import os
from dataclasses import dataclass, replace
from pathlib import Path

import pandas as pd

from tracker.publish import DATASETS

logger = logging.getLogger(__name__)

REPO_ID = DATASETS["nc"]
"""Dataset that the dashboard reads."""

EVENTS_FILE = "events.parquet"
QUALITY_FILE = "quality.parquet"
SNAPSHOT_INFO = "snapshot.json"
DEFAULT_REVISION = "main"
REVISION_VARIABLE = "HYBRID_TRACKER_REVISION"
SNAPSHOT_VARIABLE = "HYBRID_TRACKER_SNAPSHOT_DIR"
SNAPSHOT_DIR = Path(__file__).resolve().parents[2] / "data" / "snapshot"

UNREACHABLE = "unreachable"
UNREADABLE = "unreadable"

CONNECTION_ERROR_NAMES = frozenset(
    {
        "ConnectError",
        "ConnectionError",
        "NetworkError",
        "OfflineModeIsEnabled",
        "Timeout",
        "TimeoutException",
        "TransportError",
    }
)
"""Names of the exception classes of the Hub client (and of its HTTP library)
that mean the Hub gave no answer, as opposed to an answer that says no."""


@dataclass(frozen=True)
class Published:
    """The published events and the revision they come from."""

    events: pd.DataFrame
    """The rows, with the columns of `tracker.schema.COLUMNS`."""

    revision: str
    """Commit hash (40 characters) of the revision read, or "unknown" for a
    snapshot with no `snapshot.json`."""

    date: str
    """Date of that commit, `YYYY-MM-DD` (UTC), or an empty text when unknown."""

    from_snapshot: bool
    """True when the Hub could not be read and the bundled snapshot was used."""

    failure: str = ""
    """Why the Hub was not used: `UNREACHABLE` when it gave no answer,
    `UNREADABLE` when it answered but the data could not be read at the revision
    (unknown revision, unreadable file). Empty when `from_snapshot` is False."""

    quality: pd.DataFrame | None = None
    """The quality table at the same revision, or None when it cannot be read
    (the snapshot, a revision before the table had the date columns)."""


def configured_revision() -> str:
    """Revision to read: the `HYBRID_TRACKER_REVISION` variable, else `main`."""
    return os.environ.get(REVISION_VARIABLE) or DEFAULT_REVISION


def snapshot_dir() -> Path:
    """Folder of the bundled snapshot.

    This is the `HYBRID_TRACKER_SNAPSHOT_DIR` variable, else `data/snapshot`.
    """
    configured = os.environ.get(SNAPSHOT_VARIABLE)
    return Path(configured) if configured else SNAPSHOT_DIR


def is_connection_failure(error: BaseException) -> bool:
    """Tell if an error means that the Hub gave no answer."""
    if isinstance(error, ConnectionError | TimeoutError):
        return True
    return any(cls.__name__ in CONNECTION_ERROR_NAMES for cls in type(error).__mro__)


def fetch(revision: str) -> tuple[Path, str, str]:
    """Download `events.parquet` at a revision and read the date of that commit.

    The revision is resolved to a commit hash first. The file is then fetched at
    that hash, so the Hugging Face disk cache serves it again with no download.

    Args:
        revision: A branch, a tag or a commit hash.

    Returns:
        The path of the local file, the commit hash and the commit date.

    Raises:
        Exception: Any error of the Hub client when the Hub cannot be reached or
            the revision does not exist.
    """
    from huggingface_hub import HfApi, hf_hub_download

    commits = HfApi().list_repo_commits(REPO_ID, repo_type="dataset", revision=revision)
    commit = commits[0]
    path = hf_hub_download(
        REPO_ID, EVENTS_FILE, repo_type="dataset", revision=commit.commit_id
    )
    return Path(path), commit.commit_id, commit.created_at.date().isoformat()


def fetch_quality(commit: str) -> pd.DataFrame:
    """Download `quality.parquet` at a commit hash and read it."""
    from huggingface_hub import hf_hub_download

    path = hf_hub_download(REPO_ID, QUALITY_FILE, repo_type="dataset", revision=commit)
    return pd.read_parquet(path)


def read_quality(commit: str) -> pd.DataFrame | None:
    """The quality table at a commit, or None when it cannot be read.

    The table is optional for the dashboard, so any error is logged and
    the dates of the sources stay empty.
    """
    try:
        return fetch_quality(commit)
    except Exception:
        logger.warning(
            "Could not read %s at %s, the dates of the sources stay empty",
            QUALITY_FILE,
            commit,
            exc_info=True,
        )
        return None


def read_snapshot(folder: Path) -> Published:
    """Read the bundled snapshot.

    Raises:
        FileNotFoundError: The folder has no `events.parquet`.
    """
    events = pd.read_parquet(folder / EVENTS_FILE)
    info_path = folder / SNAPSHOT_INFO
    info = (
        json.loads(info_path.read_text(encoding="utf-8")) if info_path.exists() else {}
    )
    return Published(
        events=events,
        revision=info.get("revision") or "unknown",
        date=info.get("date", ""),
        from_snapshot=True,
    )


def load_published(revision: str, fallback: Path) -> Published:
    """Load the published events at a revision, or the snapshot when the Hub fails.

    Args:
        revision: A branch, a tag or a commit hash.
        fallback: Folder of the bundled snapshot.

    Returns:
        The events and the revision used. `from_snapshot` is True when the
        read from the Hub failed, and `failure` says if the Hub gave no answer
        or the data could not be read at that revision.

    Raises:
        FileNotFoundError: The Hub failed and the snapshot folder has no file.
    """
    try:
        path, commit, committed = fetch(revision)
        events = pd.read_parquet(path)
    except Exception as error:
        failure = UNREACHABLE if is_connection_failure(error) else UNREADABLE
        logger.warning(
            "Could not read %s at %s from the Hub, reading the snapshot in %s",
            REPO_ID,
            revision,
            fallback,
            exc_info=True,
        )
        return replace(read_snapshot(fallback), failure=failure)
    return Published(
        events, commit, committed, from_snapshot=False, quality=read_quality(commit)
    )
