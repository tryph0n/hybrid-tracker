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

A problem of one source does not fail the run. Each source has a list of causes:
no clean table (the source is left out of the build and gets a row of zeros),
a refresh that failed after a download (the record of `tracker.ingest`), no
row in its clean table, or, for a live source, no successful fetch for more
than `MAX_FETCH_AGE_DAYS` days. The causes are in the `cause` column of the
row, in the run summary, and in `alerts.json` in the build folder: an object
that maps each source that has a cause to the list of its causes, empty when no
source has one. A static source is refreshed by hand: the three-day rule does
not apply to it. The run fails only when no source has a clean table.

A context source (`Source.table`) has no events: its row counts the rows of its
month files, gives the newest stored day as `latest_event_date`, and leaves the
columns of events empty. Its `downloaded_at` comes from the record that
`stanford.write_months` writes once it reduced the fetched months, blank when
there is none. It has a cause when no run stored a new day for more than
`MAX_NEW_DAY_AGE_DAYS` days, since the days fetched again keep `last_fetch_at`
recent.
"""

import io
import json
import logging
import os
from collections.abc import Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from tracker import clock
from tracker.build import family_sources
from tracker.errors import UserError
from tracker.ingest import clean_path, read_failure, read_lineage
from tracker.schema import REQUIRED
from tracker.sources import FAMILIES, SOURCES
from tracker.stanford import new_day_stored_at, table_days
from tracker.store import Store

logger = logging.getLogger(__name__)

MAX_FETCH_AGE_DAYS = 3
MAX_NEW_DAY_AGE_DAYS = 7

QUALITY_PATH = "quality/quality.parquet"
EVENT_IDS_PATH = "quality/event_ids.parquet"
SUMMARY_VAR = "GITHUB_STEP_SUMMARY"
ALERTS_NAME = "alerts.json"

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
TEXT_COLUMNS = [
    "run_at",
    "source",
    "latest_event_date",
    "last_fetch_at",
    "downloaded_at",
    "updated_by_source",
    "cause",
]
COLUMNS = [
    "run_at",
    "source",
    *COUNT_COLUMNS,
    *EMPTY_COLUMNS,
    "latest_event_date",
    "last_fetch_at",
    "downloaded_at",
    "updated_by_source",
    "cause",
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
    lineage: Mapping[str, Any] | None = None,
    causes: Sequence[str] = (),
) -> dict[str, object]:
    """The quality row of one source for this run.

    Args:
        name: Key of `SOURCES`.
        events: Its clean table.
        previous_ids: Event ids of the previous run, None for a first run.
        fetched: Time of its last successful fetch.
        run_at: Time of this run, ISO format.
        lineage: The lineage record of its clean table, None when it has none.
            It gives `downloaded_at` and `updated_by_source`, blank otherwise.
        causes: Why the source has an alert, joined in the `cause` column.
    """
    lineage = lineage or {}
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
    row["downloaded_at"] = lineage.get("downloaded_at")
    row["updated_by_source"] = lineage.get("updated_by_source")
    row["cause"] = "; ".join(causes) or None
    return row


def context_row(
    name: str,
    days: pd.DataFrame,
    fetched: pd.Timestamp | None,
    run_at: str,
    lineage: Mapping[str, Any] | None = None,
    causes: Sequence[str] = (),
) -> dict[str, object]:
    """The quality row of one context source for this run.

    Args:
        name: Key of `SOURCES`.
        days: A frame with the `day` column of its month files.
        fetched: Time of its last successful fetch.
        run_at: Time of this run, ISO format.
        lineage: The record of its month files, None when it has none. It gives
            `downloaded_at`, blank otherwise.
        causes: Why the source has an alert, joined in the `cause` column.
    """
    row: dict[str, object] = {
        "run_at": run_at,
        "source": name,
        "rows": len(days),
        "rows_added": 0,
        "rows_removed": 0,
        "none_events": 0,
        "none_added": 0,
    }
    row.update({f"empty_{column}": None for column in KEY_COLUMNS})
    row["latest_event_date"] = None if days.empty else str(days["day"].max())
    row["last_fetch_at"] = None if fetched is None else fetched.isoformat()
    row["downloaded_at"] = (lineage or {}).get("downloaded_at")
    row["updated_by_source"] = None
    row["cause"] = "; ".join(causes) or None
    return row


def typed(table: pd.DataFrame) -> pd.DataFrame:
    """The table with the column order and types of `COLUMNS`.

    A column that the table lacks, as in the rows of the runs before it existed,
    is added with blank values.
    """
    table = table.reindex(columns=COLUMNS).copy()
    for column in COUNT_COLUMNS:
        table[column] = table[column].astype("int64")
    for column in EMPTY_COLUMNS:
        table[column] = table[column].astype("float64")
    for column in TEXT_COLUMNS:
        table[column] = (
            table[column].astype("object").where(table[column].notna(), None)
        )
    return table


def causes(
    name: str,
    events: pd.DataFrame | None,
    fetched: pd.Timestamp | None,
    failure: Mapping[str, Any] | None,
    now: pd.Timestamp,
    new_day: pd.Timestamp | None = None,
) -> list[str]:
    """Why a source has an alert in this run, empty when it has none.

    Args:
        name: Key of `SOURCES`.
        events: Its clean table, None when it has none. For a context source,
            a frame with its `day` column.
        fetched: Time of its last successful fetch.
        failure: The record of its last refresh error, None when it succeeded.
        now: Time of this run.
        new_day: For a context source, time of the latest run that stored a new
            day. A successful fetch does not move it.
    """
    found = []
    if events is None:
        found.append("no clean table")
    elif events.empty:
        found.append("no row in the clean table")
    if failure is not None:
        found.append(f"refresh failed: {failure['error']}")
    if SOURCES[name].kind == "live":
        if fetched is None:
            found.append("no successful fetch")
        elif now - fetched > timedelta(days=MAX_FETCH_AGE_DAYS):
            found.append(
                f"no successful fetch since {fetched.isoformat()} "
                f"(more than {MAX_FETCH_AGE_DAYS} days)"
            )
    if (
        SOURCES[name].table == "context"
        and new_day is not None
        and now - new_day > timedelta(days=MAX_NEW_DAY_AGE_DAYS)
    ):
        found.append(
            f"no new day stored since {new_day.isoformat()} "
            f"(more than {MAX_NEW_DAY_AGE_DAYS} days)"
        )
    return found


def alerts_summary(alerts: Mapping[str, Sequence[str]]) -> str:
    """The run summary section that names each source with a cause."""
    lines = ["### Sources with an alert", ""]
    for name, found in alerts.items():
        lines += [f"- `{name}`: {cause}" for cause in found]
    return "\n".join([*lines, ""])


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

    A source with no clean table gets a row with no events and the cause
    `no clean table`. Every problem of one source goes to the `cause` column and
    to `<out>/alerts.json`; none of them fails the run.

    Args:
        store: The raw store: clean tables, manifests, failure records, and the
            master copy of the quality table.
        out: The folder of `tracker build`. Each family folder gets
            `quality.parquet`, created if needed, and the folder gets
            `alerts.json`.

    Returns:
        The rows of this run.

    Raises:
        UserError: No source has a clean table. Nothing is written.
    """
    now = clock.utc_now()
    run_at = now.isoformat()
    history = read_table(store, QUALITY_PATH)
    stored_ids = read_table(store, EVENT_IDS_PATH)
    seen = set() if history is None else set(history["source"])
    tables = {
        name: table_days(store)
        if source.table == "context"
        else read_table(store, clean_path(name))
        for name, source in SOURCES.items()
    }
    if all(events is None for events in tables.values()):
        raise UserError("no source has a clean table, run `tracker ingest`")

    rows = []
    all_ids = []
    alerts = {}
    for name, events in tables.items():
        fetched = last_fetch(store, name)
        context = SOURCES[name].table == "context"
        new_day = new_day_stored_at(store) if context else None
        found = causes(name, events, fetched, read_failure(name, store), now, new_day)
        if found:
            alerts[name] = found
        if context:
            days = pd.DataFrame({"day": []}) if events is None else events
            rows.append(
                context_row(
                    name, days, fetched, run_at, read_lineage(name, store), found
                )
            )
            continue
        if events is None:
            events = pd.DataFrame(
                {column: [] for column in ["event_id", "date", *KEY_COLUMNS]}
            )
        previous = None
        if name in seen and stored_ids is not None:
            previous = set(stored_ids.loc[stored_ids["source"] == name, "event_id"])
        rows.append(
            source_row(
                name,
                events,
                previous,
                fetched,
                run_at,
                read_lineage(name, store),
                found,
            )
        )
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
    (out / ALERTS_NAME).write_text(
        json.dumps(alerts, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    write_summary(render(current) + (alerts_summary(alerts) if alerts else ""))
    return current
