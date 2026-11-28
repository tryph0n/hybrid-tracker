"""Read the published tables from the public Hugging Face dataset.

The dashboard reads `events.parquet` of the NonCommercial dataset, which holds
the rows of both licence families, and its `quality.parquet` for the dates of
each source (blank when that file cannot be read). For a context source
(`Source.table`), it reads the first and the newest day from the Parquet
statistics of the oldest and the newest month file, not the rows (blank when
those files cannot be read). When the Hub cannot be reached, it reads a
snapshot of the same file that the image build bundles in a folder, with the
revision and the date of that snapshot in `snapshot.json`; the snapshot has no
month file, so the days of each context source are blank.

This module has no Streamlit code: the app adds the cache.
"""

import datetime
import json
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from tracker.build import CONTEXT_TABLES
from tracker.errors import UserError
from tracker.publish import DATASETS
from tracker.sources import SOURCES

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

    context: dict[str, tuple[datetime.date, datetime.date] | None] = field(
        default_factory=dict
    )
    """The first and the newest day of each context source that has month files
    at the same revision. None when its files cannot be read (a read error, or
    the snapshot, which has no month file). A source with no month file at the
    revision has no entry."""


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


def fetch_month_ends(commit: str, folder: str) -> dict[str, Path]:
    """Download the oldest and the newest month file of a table at a commit.

    Args:
        commit: A commit hash.
        folder: The folder of the table, as `CONTEXT_TABLES` gives it.

    Returns:
        The repository path to the local file, for the two files (one when the
        table has one month). Empty when the table has no month file.
    """
    from huggingface_hub import HfApi, hf_hub_download

    pattern = re.compile(rf"{re.escape(folder)}/\d{{4}}-\d{{2}}\.parquet")
    names = sorted(
        name
        for name in HfApi().list_repo_files(
            REPO_ID, repo_type="dataset", revision=commit
        )
        if pattern.fullmatch(name)
    )
    return {
        name: Path(hf_hub_download(REPO_ID, name, repo_type="dataset", revision=commit))
        for name in dict.fromkeys(names[:1] + names[-1:])
    }


def column_range(path: Path, column: str) -> tuple[datetime.date, datetime.date]:
    """The smallest and the largest value of a date column, from the statistics.

    Raises:
        UserError: The file has no row group, or a row group has no statistics.
    """
    metadata = pq.ParquetFile(path).metadata
    position = metadata.schema.names.index(column)
    stats = [
        metadata.row_group(index).column(position).statistics
        for index in range(metadata.num_row_groups)
    ]
    if not stats or any(stat is None or not stat.has_min_max for stat in stats):
        raise UserError(f"{path.name}: no statistics for {column}")
    return min(stat.min for stat in stats), max(stat.max for stat in stats)


def day_bounds(files: Mapping[str, Path]) -> tuple[datetime.date, datetime.date]:
    """The first and the newest day of a table from its oldest and newest month file.

    Args:
        files: Repository path (`<table>/<YYYY>-<MM>.parquet`) to local file.
            The oldest name gives the first day, the newest name the newest day.
    """
    names = sorted(files)
    return (
        column_range(files[names[0]], "day")[0],
        column_range(files[names[-1]], "day")[1],
    )


def context_sources() -> list[str]:
    """The context sources of the registry that have a table folder."""
    return [
        name
        for name in CONTEXT_TABLES
        if name in SOURCES and SOURCES[name].table == "context"
    ]


def read_context(
    commit: str,
) -> dict[str, tuple[datetime.date, datetime.date] | None]:
    """The first and the newest day of each context source at a commit.

    A source with no month file at the commit has no entry. The days are
    optional for the dashboard, so a read error is logged and the source gets
    None: it is listed with blank days.
    """
    bounds: dict[str, tuple[datetime.date, datetime.date] | None] = {}
    for name in context_sources():
        try:
            files = fetch_month_ends(commit, CONTEXT_TABLES[name])
            if files:
                bounds[name] = day_bounds(files)
        except Exception:
            logger.warning(
                "Could not read the month files of %s at %s, its days are blank",
                name,
                commit,
                exc_info=True,
            )
            bounds[name] = None
    return bounds


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
        context=dict.fromkeys(context_sources()),
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
        events,
        commit,
        committed,
        from_snapshot=False,
        quality=read_quality(commit),
        context=read_context(commit),
    )
