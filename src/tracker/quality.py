"""Quality table and run summary: one row per source and per run.

Run through the `tracker` command: `uv run tracker quality [--out build]`, after
`tracker build`. A raw store is needed (see `tracker.store`).

The master copy of the table is `quality/quality.parquet` in the raw store. Each
run appends its rows, so the table keeps the trend. The raw store also keeps
`quality/event_ids.parquet`, the event ids of the previous run, which the next
run compares with to count rows added and removed. The counts read the clean
tables, so a refresh that the shrink guard rejected leaves the clean table
unchanged and counts as no change. The table is also written to
`<out>/<family>/quality.parquet` for each family, with the rows of the sources
of that family. The run summary is Markdown, appended to the file named by
`GITHUB_STEP_SUMMARY` when it is set, and logged otherwise.

The run fails, after the table and the summary are written, when a source has
no row, or when a live source has had no successful fetch for more than
`MAX_FETCH_AGE_DAYS` days. A static source is refreshed by hand: only the empty
check applies to it.
"""

import io
import logging
import os
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from tracker import clock
from tracker.build import family_sources
from tracker.errors import UserError
from tracker.ingest import clean_path
from tracker.schema import REQUIRED
from tracker.sources import FAMILIES, SOURCES
from tracker.store import Store

logger = logging.getLogger(__name__)

MAX_FETCH_AGE_DAYS = 3

QUALITY_PATH = "quality/quality.parquet"
EVENT_IDS_PATH = "quality/event_ids.parquet"
SUMMARY_VAR = "GITHUB_STEP_SUMMARY"

KEY_COLUMNS = [*REQUIRED, "lat", "lon", "actors"]
"""Columns whose share of empty values is counted. `lat` and `lon` are here
because an event with no coordinates is not on the map. `actors` counts only
the rows whose attribution is not `none`."""

COUNT_COLUMNS = [
    "rows",
    "rows_added",
    "rows_removed",
    "none_events",
    "none_added",
]
EMPTY_COLUMNS = [f"empty_{column}" for column in KEY_COLUMNS]
TEXT_COLUMNS = ["run_at", "source", "latest_event_date", "last_fetch_at"]
COLUMNS = [
    "run_at",
    "source",
    *COUNT_COLUMNS,
    *EMPTY_COLUMNS,
    "latest_event_date",
    "last_fetch_at",
]


def read_table(store: Store, path: str) -> pd.DataFrame | None:
    """A Parquet file of the raw store, or None when it does not exist."""
    content = store.backend.read(path)
    return None if content is None else pd.read_parquet(io.BytesIO(content))


def to_parquet(frame: pd.DataFrame) -> bytes:
    """The bytes of a frame as a Parquet file."""
    buffer = io.BytesIO()
    frame.to_parquet(buffer, index=False)
    return buffer.getvalue()


def empty_share(values: pd.Series, rows: pd.Series | None = None) -> float | None:
    """Share of missing or empty cells in `values`, None when there is no row.

    Args:
        values: The cells.
        rows: Which cells count, as a boolean series. All of them when omitted.
    """
    if rows is not None:
        values = values[rows]
    if values.empty:
        return None
    return float((values.isna() | values.eq("")).mean())


def last_fetch(store: Store, name: str) -> pd.Timestamp | None:
    """Time of the latest successful fetch of a source, from its manifest.

    A line with an HTTP status of 200 to 299 counts, also when the payload was
    identical and not saved again.
    """
    times = [
        pd.Timestamp(line["fetched_at"])
        for line in store.manifest(name)
        if 200 <= line["status"] < 300
    ]
    return max(times) if times else None


def source_row(
    name: str,
    events: pd.DataFrame,
    previous_ids: set[str] | None,
    fetched: pd.Timestamp | None,
    run_at: str,
) -> dict[str, object]:
    """The quality row of one source for this run.

    Args:
        name: Key of `SOURCES`.
        events: Its clean table.
        previous_ids: Event ids of the previous run, None for a first run.
        fetched: Time of its last successful fetch.
        run_at: Time of this run, ISO format.
    """
    ids = set(events["event_id"])
    is_none = events["attribution"] == "none"
    new = ids if previous_ids is None else ids - previous_ids
    removed = set() if previous_ids is None else previous_ids - ids
    row: dict[str, object] = {
        "run_at": run_at,
        "source": name,
        "rows": len(events),
        "rows_added": len(new),
        "rows_removed": len(removed),
        "none_events": int(is_none.sum()),
        "none_added": int((is_none & events["event_id"].isin(new)).sum()),
    }
    for column in KEY_COLUMNS:
        counted = ~is_none if column == "actors" else None
        row[f"empty_{column}"] = empty_share(events[column], counted)
    row["latest_event_date"] = None if events.empty else str(events["date"].max())
    row["last_fetch_at"] = None if fetched is None else fetched.isoformat()
    return row


def typed(table: pd.DataFrame) -> pd.DataFrame:
    """The table with the column order and types of `COLUMNS`."""
    table = table[COLUMNS].copy()
    for column in COUNT_COLUMNS:
        table[column] = table[column].astype("int64")
    for column in EMPTY_COLUMNS:
        table[column] = table[column].astype("float64")
    for column in TEXT_COLUMNS:
        table[column] = table[column].astype("object")
    return table


def problems(table: pd.DataFrame, now: pd.Timestamp) -> list[str]:
    """What fails the run, one message per source and cause.

    Args:
        table: The rows of this run.
        now: Time of this run.
    """
    found = []
    limit = timedelta(days=MAX_FETCH_AGE_DAYS)
    for row in table.to_dict("records"):
        name = str(row["source"])
        fetched = row["last_fetch_at"]
        if row["rows"] == 0:
            found.append(f"{name}: no row in the clean table")
        if SOURCES[name].kind != "live":
            continue
        if fetched is None:
            found.append(f"{name}: no successful fetch")
        elif now - pd.Timestamp(str(fetched)) > limit:
            found.append(
                f"{name}: no successful fetch since {fetched} "
                f"(more than {MAX_FETCH_AGE_DAYS} days)"
            )
    return found


def cell(value: Any) -> str:
    """A table cell as text: a share as a percentage, a missing value as empty."""
    if value is None or pd.isna(value):
        return ""
    if isinstance(value, float):
        return f"{value:.1%}"
    return str(value)


def render(table: pd.DataFrame) -> str:
    """The rows of one run as a Markdown table, with the count of `none` events."""
    header = "| " + " | ".join(COLUMNS) + " |"
    divider = "|" + "---|" * len(COLUMNS)
    lines = [
        "## Data quality",
        "",
        header,
        divider,
        *(
            "| " + " | ".join(cell(row[column]) for column in COLUMNS) + " |"
            for _, row in table.iterrows()
        ),
        "",
        (
            f"Events with attribution none: {int(table['none_events'].sum())} "
            f"({int(table['none_added'].sum())} new in this run)."
        ),
        "",
    ]
    return "\n".join(lines)


def write_summary(markdown: str, env: Mapping[str, str] | None = None) -> None:
    """Append the Markdown to the run summary file, or log it when there is none."""
    env = os.environ if env is None else env
    path = env.get(SUMMARY_VAR)
    if path:
        with open(path, "a", encoding="utf-8") as summary:
            summary.write(markdown)
    else:
        logger.info("%s", markdown)


def run(store: Store, out: Path) -> pd.DataFrame:
    """Compute the rows of this run, write them everywhere, then check them.

    Args:
        store: The raw store: clean tables, manifests, and the master copy of
            the quality table.
        out: The folder of `tracker build`. Each family folder gets
            `quality.parquet`, created if needed.

    Returns:
        The rows of this run.

    Raises:
        ValueError: A source has no clean table, or a source has no row, or a
            live source has no recent successful fetch. The table and the
            summary are written before the second and third errors.
    """
    now = clock.utc_now()
    run_at = now.isoformat()
    history = read_table(store, QUALITY_PATH)
    stored_ids = read_table(store, EVENT_IDS_PATH)
    seen = set() if history is None else set(history["source"])

    rows = []
    all_ids = []
    for name in SOURCES:
        events = read_table(store, clean_path(name))
        if events is None:
            raise UserError(f"{name}: no clean table, run `tracker ingest {name}`")
        previous = None
        if name in seen and stored_ids is not None:
            previous = set(stored_ids.loc[stored_ids["source"] == name, "event_id"])
        rows.append(source_row(name, events, previous, last_fetch(store, name), run_at))
        all_ids.append(pd.DataFrame({"source": name, "event_id": events["event_id"]}))
    current = typed(pd.DataFrame(rows))
    table = current if history is None else pd.concat([typed(history), current])
    table = table.reset_index(drop=True)

    ids = pd.concat(all_ids, ignore_index=True)
    store.backend.commit(
        {QUALITY_PATH: to_parquet(table), EVENT_IDS_PATH: to_parquet(ids)},
        f"quality: run at {run_at}",
    )
    for family in FAMILIES:
        folder = out / family
        folder.mkdir(parents=True, exist_ok=True)
        in_family = table["source"].isin(family_sources(family))
        (folder / "quality.parquet").write_bytes(to_parquet(table[in_family]))
    write_summary(render(current))

    found = problems(current, now)
    if found:
        raise UserError("Quality check failed:\n" + "\n".join(found))
    return current
