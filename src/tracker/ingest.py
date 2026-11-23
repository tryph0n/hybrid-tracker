"""Fetch sources and store them as data/events/<source>.csv.

Usage, from the repository root with PYTHONPATH=src:

- `python -m tracker.ingest live`: every live source (scheduled pipeline)
- `python -m tracker.ingest static`: every static source (run by hand)
- `python -m tracker.ingest eodi`: a single source
- `python -m tracker.ingest check`: validate stored files, fetch nothing
"""

import sys

from tracker.schema import validate
from tracker.sources import SOURCES, events_path, read_all_events, read_events

# A refresh that loses more than this share of the stored events is rejected:
# it is more likely an upstream outage than a real correction. Kept small
# because one missing upstream file can be only ~15% of a source.
MAX_SHRINK = 0.05


def ingest(name: str) -> None:
    """Fetch one source, validate it and replace its stored file.

    Writes `data/events/<name>.csv` sorted by `event_id` (the folder is created
    if needed) and prints "<name>: N events". Nothing is written if the fetch,
    `validate` or the shrink check fails. Errors of the source's `fetch` pass
    through.

    Args:
        name: Key of `SOURCES`.

    Raises:
        KeyError: `name` is not a source.
        ValueError: The events fail `validate`, or the fetch loses more than
            the `MAX_SHRINK` share of the stored events.
    """
    events = SOURCES[name].fetch().sort_values("event_id")
    validate(events)

    path = events_path(name)
    if path.exists():
        previous = len(read_events(name))
        if len(events) < previous * (1 - MAX_SHRINK):
            raise ValueError(f"{name}: {len(events)} events, {previous} before")

    path.parent.mkdir(parents=True, exist_ok=True)
    events.to_csv(path, index=False, lineterminator="\n")
    print(f"{name}: {len(events)} events")


def check() -> None:
    """Validate what is stored, all sources together."""
    events = read_all_events()
    validate(events)
    print(f"{len(events)} events, {events['source'].nunique()} sources: valid")


def main(target: str) -> None:
    """Run one command: a source name, `live`, `static` or `check`.

    Raises:
        SystemExit: `target` is not a command. The message is the usage text.
    """
    if target == "check":
        check()
    elif target in SOURCES:
        ingest(target)
    elif target in ("live", "static"):
        for name, source in SOURCES.items():
            if source.kind == target:
                ingest(name)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) == 2 else "")
