"""Download sources, keep their payloads in the raw store and write clean tables.

Run through the `tracker` command, from the repository root after `uv sync`:

- `uv run tracker ingest live`: every live source (scheduled pipeline)
- `uv run tracker ingest static`: every static source (run by hand)
- `uv run tracker ingest eodi`: a single source
- `uv run tracker ingest check`: validate stored files, download nothing

Each source step needs a raw store: set `TRACKER_STORE` (see `tracker.store`).
"""

import io
import logging

import pandas as pd

from tracker.schema import validate
from tracker.sources import SOURCES, events_path, read_all_events, read_events
from tracker.store import Store, open_store

logger = logging.getLogger(__name__)

# A refresh that loses more than this share of the stored events is rejected:
# it is more likely an upstream outage than a real correction. Kept small
# because one missing upstream file can be only ~15% of a source.
MAX_SHRINK = 0.05

TARGETS = ("live", "static", "check")


def clean_path(name: str) -> str:
    """Path of the clean table of one source in the raw store."""
    return f"clean/{name}.parquet"


def previous_count(name: str, stored: bytes | None) -> int | None:
    """Number of events stored before this run, or None for a first ingest.

    The clean table of the raw store counts first. Until a source has one, the
    CSV file of `data/events` counts.
    """
    if stored is not None:
        return len(pd.read_parquet(io.BytesIO(stored)))
    if events_path(name).exists():
        return len(read_events(name))
    return None


def ingest(name: str, store: Store) -> None:
    """Download one source, keep its payloads, then write its clean table.

    The payloads are saved first: a payload that fails to map stays in the raw
    store, so the mapping can be fixed and replayed. Then the events of the
    payloads are validated, and written sorted by `event_id` to
    `clean/<name>.parquet` in the store and to `data/events/<name>.csv` (the
    folder is created if needed). Nothing is written to either if `validate`
    or the shrink check fails.

    Args:
        name: Key of `SOURCES`.
        store: The raw store.

    Raises:
        KeyError: `name` is not a source.
        ValueError: The events fail `validate`, or the download loses more than
            the `MAX_SHRINK` share of the stored events.
    """
    source = SOURCES[name]
    payloads = source.extract()
    store.save_many(payloads)
    events = source.normalize({p.name: p.content for p in payloads})
    events = events.sort_values("event_id", ignore_index=True)
    validate(events)

    stored = store.backend.read(clean_path(name))
    previous = previous_count(name, stored)
    if previous is not None and len(events) < previous * (1 - MAX_SHRINK):
        raise ValueError(f"{name}: {len(events)} events, {previous} before")

    table = io.BytesIO()
    events.to_parquet(table, index=False)
    if stored != table.getvalue():
        store.backend.commit(
            {clean_path(name): table.getvalue()}, f"clean: {name} table"
        )

    path = events_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    events.to_csv(path, index=False, lineterminator="\n")
    logger.info("%s: %d events", name, len(events))


def check() -> None:
    """Validate what is stored, all sources together."""
    events = read_all_events()
    validate(events)
    logger.info("%d events, %d sources: valid", len(events), events["source"].nunique())


def run(target: str) -> None:
    """Run one target: a source name, `live`, `static` or `check`.

    Raises:
        ValueError: `target` is not a target.
    """
    if target == "check":
        check()
    elif target in SOURCES:
        ingest(target, open_store())
    elif target in ("live", "static"):
        store = open_store()
        for name, source in SOURCES.items():
            if source.kind == target:
                ingest(name, store)
    else:
        raise ValueError(f"Unknown target {target!r}")
