"""Read the published tables from the public Hugging Face dataset.

The dashboard reads `events.parquet` of the NonCommercial dataset, which holds
the rows of both licence families. When the Hub cannot be reached, it reads a
snapshot of the same file that the image build bundles in a folder, with the
revision and the date of that snapshot in `snapshot.json`.

This module has no Streamlit code: the app adds the cache.
"""

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from tracker.publish import DATASETS

logger = logging.getLogger(__name__)

REPO_ID = DATASETS["nc"]
"""Dataset that the dashboard reads."""

EVENTS_FILE = "events.parquet"
SNAPSHOT_INFO = "snapshot.json"
DEFAULT_REVISION = "main"
REVISION_VARIABLE = "HYBRID_TRACKER_REVISION"
SNAPSHOT_VARIABLE = "HYBRID_TRACKER_SNAPSHOT_DIR"
SNAPSHOT_DIR = Path(__file__).resolve().parents[2] / "data" / "snapshot"


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


def configured_revision() -> str:
    """Revision to read: the `HYBRID_TRACKER_REVISION` variable, else `main`."""
    return os.environ.get(REVISION_VARIABLE) or DEFAULT_REVISION


def snapshot_dir() -> Path:
    """Folder of the bundled snapshot.

    This is the `HYBRID_TRACKER_SNAPSHOT_DIR` variable, else `data/snapshot`.
    """
    configured = os.environ.get(SNAPSHOT_VARIABLE)
    return Path(configured) if configured else SNAPSHOT_DIR


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
        read from the Hub failed.

    Raises:
        FileNotFoundError: The Hub failed and the snapshot folder has no file.
    """
    try:
        path, commit, committed = fetch(revision)
        events = pd.read_parquet(path)
    except Exception:
        logger.warning(
            "Could not read %s at %s from the Hub, reading the snapshot in %s",
            REPO_ID,
            revision,
            fallback,
            exc_info=True,
        )
        return read_snapshot(fallback)
    return Published(events, commit, committed, from_snapshot=False)
