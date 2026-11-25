"""Download sources, keep their payloads in the raw store and write clean tables.

Run through the `tracker` command, from the repository root after `uv sync`:

- `uv run tracker ingest live`: every live source (scheduled pipeline)
- `uv run tracker ingest static`: every static source and every reference
  dataset (run by hand)
- `uv run tracker ingest eodi`: a single source or reference dataset
  (`geonames`: payloads only, no clean table)
- `uv run tracker ingest static --if-missing`: only what is not in the raw store:
  a source with no clean table, a reference dataset with no payload (scheduled
  pipeline)
- `uv run tracker ingest check`: validate stored files, download nothing
- `uv run tracker rebuild [source]`: write the clean tables again from the
  stored payloads, download nothing

Each source step needs a raw store: set `TRACKER_STORE` (see `tracker.store`).
"""

import io
import logging
from collections.abc import Mapping

import pandas as pd

from tracker.schema import validate
from tracker.sources import (
    REFERENCES,
    SOURCES,
    events_path,
    read_all_events,
    read_events,
)
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


def write_clean(
    name: str,
    files: Mapping[str, bytes],
    store: Store,
    allow_shrink: bool = False,
) -> None:
    """Map the payloads of one source, validate them and write its clean table.

    The events are sorted by `event_id` and written to `clean/<name>.parquet`
    in the store and to `data/events/<name>.csv` (the folder is created if
    needed). Nothing is written to either if `validate` or the shrink check
    fails. `ingest` and `rebuild` both end here.

    Args:
        name: Key of `SOURCES`.
        files: Payload file name to bytes, as `normalize` takes them.
        store: The raw store.
        allow_shrink: Skip the shrink check. A warning in the log says so when
            the check would have failed.

    Raises:
        ValueError: The events fail `validate`, or they lose more than the
            `MAX_SHRINK` share of the stored events and `allow_shrink` is false.
    """
    events = SOURCES[name].normalize(files)
    events = events.sort_values("event_id", ignore_index=True)
    validate(events)

    stored = store.backend.read(clean_path(name))
    previous = previous_count(name, stored)
    if previous is not None and len(events) < previous * (1 - MAX_SHRINK):
        message = f"{name}: {len(events)} events, {previous} before"
        if not allow_shrink:
            raise ValueError(message)
        logger.warning("Shrink guard skipped by override: %s", message)

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


def ingest(name: str, store: Store) -> None:
    """Download one source, keep its payloads, then write its clean table.

    The payloads are saved first: a payload that fails to map stays in the raw
    store, so the mapping can be fixed and replayed with `rebuild`.

    Args:
        name: Key of `SOURCES`.
        store: The raw store.

    Raises:
        KeyError: `name` is not a source.
        ValueError: The events fail `validate`, or the download loses more than
            the `MAX_SHRINK` share of the stored events.
    """
    payloads = SOURCES[name].extract()
    store.save_many(payloads)
    write_clean(name, {p.name: p.content for p in payloads}, store)


def ingest_reference(name: str, store: Store) -> None:
    """Download one reference dataset and keep its payloads in the raw store.

    The store adds the SHA-256 and the date of the download to its manifest. A
    file identical to the latest stored one adds a manifest line only.

    Args:
        name: Key of `REFERENCES`.
        store: The raw store.

    Raises:
        KeyError: `name` is not a reference dataset.
    """
    for line in store.save_many(REFERENCES[name].extract()):
        logger.info("%s: %s sha256 %s", name, line["name"], line["sha256"])


def latest_payloads(name: str, store: Store) -> dict[str, bytes]:
    """The latest stored payload of each file of a source, empty if it has none."""
    files: dict[str, bytes] = {}
    for file in sorted({line["name"] for line in store.payloads(name)}):
        content = store.latest(name, file)
        if content is not None:
            files[file] = content
    return files


def rebuild(name: str | None, store: Store, allow_shrink: bool = False) -> None:
    """Write the clean tables again from the latest stored payloads, offline.

    Nothing is downloaded: with the local store, nothing uses the network. Only
    the latest payload of each file is read, because each fetch is a full
    snapshot. The run stops at the first source that fails, as `ingest` does.

    Args:
        name: Key of `SOURCES`, or None for every source. A source with no
            stored payload is skipped with a warning when `name` is None.
        store: The raw store.
        allow_shrink: Skip the shrink check, for a mapping fix that drops rows
            on purpose. The log records its use.

    Raises:
        KeyError: `name` is not a source.
        ValueError: `name` has no stored payload, or the events fail `validate`,
            or they lose more than the `MAX_SHRINK` share of the stored events
            and `allow_shrink` is false.
    """
    if name is not None and name not in SOURCES:
        raise KeyError(name)
    for key in SOURCES if name is None else [name]:
        files = latest_payloads(key, store)
        if not files:
            if name is not None:
                raise ValueError(
                    f"{key}: no stored payload, run `tracker ingest {key}`"
                )
            logger.warning("%s: no stored payload, skipped", key)
            continue
        write_clean(key, files, store, allow_shrink)


def check() -> None:
    """Validate what is stored, all sources together."""
    events = read_all_events()
    validate(events)
    logger.info("%d events, %d sources: valid", len(events), events["source"].nunique())


def run(target: str, if_missing: bool = False) -> None:
    """Run one target: a source or reference name, `live`, `static` or `check`.

    `static` runs the static sources and the reference datasets.

    Args:
        target: The target to run.
        if_missing: Skip each source that already has a clean table, and each
            reference dataset that already has a payload, in the raw store:
            it is not downloaded again.

    Raises:
        ValueError: `target` is not a target, or `if_missing` is set with `check`.
    """
    if target == "check":
        if if_missing:
            raise ValueError("`--if-missing` does not apply to `check`")
        check()
        return
    if target in SOURCES or target in REFERENCES:
        names = [target]
    elif target in ("live", "static"):
        names = [n for n, source in SOURCES.items() if source.kind == target]
        if target == "static":
            names += list(REFERENCES)
    else:
        raise ValueError(f"Unknown target {target!r}")
    store = open_store()
    for name in names:
        if name in REFERENCES:
            if if_missing and store.payloads(name):
                logger.info("%s: payload in the store, skipped", name)
                continue
            ingest_reference(name, store)
        elif if_missing and store.backend.read(clean_path(name)) is not None:
            logger.info("%s: clean table in the store, skipped", name)
        else:
            ingest(name, store)
