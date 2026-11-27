"""Download sources, keep their payloads in the raw store and write clean tables.

Run through the `tracker` command, from the repository root after `uv sync`:

- `uv run tracker ingest live`: every live source (scheduled pipeline)
- `uv run tracker ingest static`: every static source and every reference
  dataset (run by hand)
- `uv run tracker ingest eodi`: a single source or reference dataset
  (`geonames`: payloads only, no clean table)
- `uv run tracker ingest static --if-missing`: only what is not in the raw store:
  a source with no clean table or no lineage record, a reference dataset with
  no payload (scheduled pipeline)
- `uv run tracker rebuild [source]`: write the clean tables again from the
  stored payloads, download nothing

Each source step needs a raw store: set `TRACKER_STORE` (see `tracker.store`).

A target that names several sources (`live`, `static`) does not stop at the
first source that fails, whatever the error (a download error, the shrink
guard, a mapping error). The source keeps its last clean table, the others are
refreshed, the error goes to `failures/<name>.json` in the raw store (read by
`tracker quality`) and to the run summary, and the command exits with code 0.
The next successful refresh of the source clears its record. A source named
alone fails the command, and so does an error of the raw store: it concerns all
the sources.
"""

import hashlib
import io
import json
import logging
from collections.abc import Mapping
from typing import Any

import pandas as pd

from tracker import clock, provenance
from tracker.errors import UserError
from tracker.schema import validate
from tracker.sources import REFERENCES, SOURCES
from tracker.store import Store, StoreError, open_store

logger = logging.getLogger(__name__)

# A refresh that loses more than this share of the stored events is rejected:
# it is more likely an upstream outage than a real correction. Kept small
# because one missing upstream file can be only ~15% of a source.
MAX_SHRINK = 0.05

TARGETS = ("live", "static")


def clean_path(name: str) -> str:
    """Path of the clean table of one source in the raw store."""
    return f"clean/{name}.parquet"


def lineage_path(name: str) -> str:
    """Path of the lineage record of the clean table of one source."""
    return f"clean/{name}.json"


def lineage(
    name: str, files: Mapping[str, bytes], store: Store, rows: int
) -> dict[str, Any]:
    """Describe where the clean table of one source comes from.

    Args:
        name: Key of `SOURCES`.
        files: Payload file name to bytes, the ones that make the table.
        store: The raw store, whose manifest gives the path of each payload.
        rows: Number of rows of the table.

    Returns:
        A record with `source`, `rows`, `code_commit` (None outside a workflow
        run), `downloaded_at` (the time of the newest download of these
        payloads, from the manifest), `updated_by_source` (the date of the last
        change that the source gives, or None) and `payloads`: the file name,
        the path in the raw store and the SHA-256 of each payload.

    Raises:
        UserError: A payload is not in the manifest of the store.
    """
    manifest = store.manifest(name)
    payloads = []
    downloads = []
    for file in sorted(files):
        digest = hashlib.sha256(files[file]).hexdigest()
        line = next(
            (
                entry
                for entry in reversed(manifest)
                if entry["name"] == file and entry["sha256"] == digest
            ),
            None,
        )
        if line is None:
            raise UserError(f"{name}: {file} is not in the manifest of the store")
        payloads.append({"name": file, "path": line["path"], "sha256": digest})
        downloads.append(pd.Timestamp(line["fetched_at"]))
    return {
        "source": name,
        "rows": rows,
        "code_commit": provenance.code_commit(),
        "downloaded_at": max(downloads).isoformat() if downloads else None,
        "updated_by_source": SOURCES[name].updated_by_source(files),
        "payloads": payloads,
    }


def failure_path(name: str) -> str:
    """Path of the record of the last refresh error of one source."""
    return f"failures/{name}.json"


def read_failure(name: str, store: Store) -> dict[str, Any] | None:
    """The error of the last refresh of one source, or None if it succeeded."""
    stored = store.backend.read(failure_path(name))
    if stored is None:
        return None
    record = dict(json.loads(stored))
    return record if record["error"] is not None else None


def write_failure(name: str, error: str | None, store: Store) -> None:
    """Record the result of a refresh: its error, or None when it succeeded.

    A success writes nothing when no error is recorded.
    """
    if error is None and read_failure(name, store) is None:
        return
    record = {
        "source": name,
        "error": error,
        "failed_at": None if error is None else clock.utc_now().isoformat(),
    }
    store.backend.commit(
        {failure_path(name): encode_lineage(record)},
        f"failures: {name}",
    )


def failure_summary(failures: Mapping[str, str]) -> str:
    """The run summary section that names each source whose refresh failed."""
    lines = ["### Sources whose refresh failed", ""]
    lines += [f"- `{name}`: {error}" for name, error in failures.items()]
    return "\n".join([*lines, ""])


def encode_lineage(record: Mapping[str, Any]) -> bytes:
    """The bytes of a lineage record: sorted keys, one trailing newline."""
    return (json.dumps(record, indent=2, sort_keys=True) + "\n").encode()


def read_lineage(name: str, store: Store) -> dict[str, Any] | None:
    """The lineage record of one source in the store, or None if it has none."""
    stored = store.backend.read(lineage_path(name))
    return None if stored is None else dict(json.loads(stored))


def without_commit(record: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """A record without its code commit, which changes at each run."""
    if record is None:
        return None
    return {key: value for key, value in record.items() if key != "code_commit"}


def previous_count(name: str, stored: bytes | None) -> int | None:
    """Number of events stored before this run, or None for a first ingest.

    The clean table of the raw store counts.
    """
    if stored is None:
        return None
    return len(pd.read_parquet(io.BytesIO(stored)))


def write_clean(
    name: str,
    files: Mapping[str, bytes],
    store: Store,
    allow_shrink: bool = False,
) -> None:
    """Map the payloads of one source, validate them and write its clean table.

    The events are sorted by `event_id` and written to `clean/<name>.parquet`
    in the store. Nothing is written if `validate` or the shrink check fails.
    `ingest` and `rebuild` both end here.

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
            raise UserError(message)
        logger.warning("Shrink guard skipped by override: %s", message)

    table = io.BytesIO()
    events.to_parquet(table, index=False)
    record = lineage(name, files, store, len(events))
    previous_record = read_lineage(name, store)
    if stored != table.getvalue() or without_commit(previous_record) != without_commit(
        record
    ):
        store.backend.commit(
            {
                clean_path(name): table.getvalue(),
                lineage_path(name): encode_lineage(record),
            },
            f"clean: {name} table",
        )

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
    snapshot. The run stops at the first source that fails: unlike `run`, it
    shows the error at once.

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
                raise UserError(f"{key}: no stored payload, run `tracker ingest {key}`")
            logger.warning("%s: no stored payload, skipped", key)
            continue
        write_clean(key, files, store, allow_shrink)


def run(target: str, if_missing: bool = False) -> dict[str, str]:
    """Run one target: a source or reference name, `live` or `static`.

    `static` runs the static sources and the reference datasets. A target with
    several sources goes on after a source that fails (see the module
    docstring).

    Args:
        target: The target to run.
        if_missing: Skip each source that already has a clean table and a
            lineage record, and each reference dataset that already has a
            payload, in the raw store: it is not downloaded again.

    Returns:
        The error of each source whose refresh failed, by source name.

    Raises:
        ValueError: `target` is not a target.
        StoreError: The raw store cannot be read or written.
    """
    if target in SOURCES or target in REFERENCES:
        names = [target]
    elif target in ("live", "static"):
        names = [n for n, source in SOURCES.items() if source.kind == target]
        if target == "static":
            names += list(REFERENCES)
    else:
        raise UserError(f"Unknown target {target!r}")
    store = open_store()
    failures: dict[str, str] = {}
    for name in names:
        if name in REFERENCES:
            if if_missing and store.payloads(name):
                logger.info("%s: payload in the store, skipped", name)
                continue
            ingest_reference(name, store)
        elif (
            if_missing
            and store.backend.read(clean_path(name)) is not None
            and read_lineage(name, store) is not None
        ):
            logger.info("%s: clean table and lineage in the store, skipped", name)
        elif len(names) == 1:
            ingest(name, store)
        else:
            try:
                ingest(name, store)
            except StoreError:
                raise
            except Exception as error:  # noqa: BLE001 - any error of one source
                logger.error("%s: refresh failed: %s", name, error)
                failures[name] = " ".join(f"{type(error).__name__}: {error}".split())
                write_failure(name, failures[name], store)
            else:
                write_failure(name, None, store)
    return failures
