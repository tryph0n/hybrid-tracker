"""Registry of data sources and access to their stored events."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from tracker import eodi, geonames, leiden
from tracker.schema import read_csv
from tracker.store import Payload

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "events"


@dataclass(frozen=True)
class Source:
    """A data source: its metadata and the two steps that turn it into events."""

    title: str
    """Name shown in the app."""

    kind: str
    """Either "live", for a source refreshed by the scheduled pipeline, or "static",
    for a frozen historical snapshot, re-ingested by hand."""

    homepage: str
    """Page of the dataset, shown as a link. It must appear in the README and in
    the dataset card."""

    licence: str
    """Licence name, for example "CC BY 4.0". It must appear in the README."""

    licence_page: str
    """Page that states the licence, as recorded in `docs/sources.md`."""

    family: str
    """Licence family, one of `FAMILIES`. It decides which public dataset gets
    the rows: `open` for a licence that allows a merge into a CC BY table,
    `nc` for a NonCommercial one."""

    citation: str
    """Credit line. It must appear word for word in the README and in the dataset
    card."""

    extract: Callable[[], list[Payload]]
    """Downloads the files of the source and returns them as they are served,
    with no mapping. Define it in the adapter module of the source."""

    normalize: Callable[[Mapping[str, bytes]], pd.DataFrame]
    """Maps the stored payloads (file name to bytes) to events in the unified
    schema, with no network access. Define it in the adapter module of the
    source."""


@dataclass(frozen=True)
class Reference:
    """Reference data: a file that the build reads to complete events.

    It is not an event source. It has no clean table, no row in the Sources
    table of the app and none in the published `sources` tables, but it needs a
    recorded licence and a credit like a source does.
    """

    title: str
    """Name of the dataset."""

    homepage: str
    """Page of the dataset. It must appear in the README and in the dataset
    card."""

    licence: str
    """Licence name. It must appear in the README."""

    licence_url: str
    """Page of the licence text. It must appear in the README."""

    licence_page: str
    """Page that states the licence, as recorded in `docs/sources.md`."""

    family: str
    """Licence family, one of `FAMILIES`."""

    citation: str
    """Credit line. It must appear word for word in the README and in the dataset
    card."""

    extract: Callable[[], list[Payload]]
    """Downloads the files of the dataset and returns them as they are served.
    Define it in the module of the dataset."""


FAMILIES = ("open", "nc")


SOURCES = {
    "eodi": Source(
        title="European Observatory of Drone Incidents",
        kind="live",
        homepage="https://droneobservatory.eu",
        licence="CC BY 4.0",
        licence_page="https://droneobservatory.eu/en",
        family="open",
        citation=(
            "Lucas Alaniz Pintos, European Observatory of Drone Incidents "
            "(EODI): drone incidents in Europe."
        ),
        extract=eodi.extract,
        normalize=eodi.normalize,
    ),
    "leiden": Source(
        title="Russian Operations Against Europe Dataset, 2022-2025",
        kind="static",
        homepage=leiden.DATASET_URL,
        licence="CC BY 4.0",
        licence_page="https://doi.org/10.7910/DVN/TQ0FMQ",
        family="open",
        citation=(
            "Bart Schuurman, Russian Operations Against Europe Dataset, "
            "2022-2025, Harvard Dataverse, V4."
        ),
        extract=leiden.extract,
        normalize=leiden.normalize,
    ),
}

REFERENCES = {
    geonames.SOURCE: Reference(
        title="GeoNames populated places (cities500)",
        homepage="https://www.geonames.org",
        licence="CC BY 4.0",
        licence_url="https://creativecommons.org/licenses/by/4.0/",
        licence_page="https://download.geonames.org/export/dump/readme.txt",
        family="open",
        citation=(
            "GeoNames, https://www.geonames.org: place names and coordinates, "
            "filtered to populated places. Provided as is, without warranty."
        ),
        extract=geonames.extract,
    ),
}
"""Reference datasets by key. Each one is a raw store source with payloads and
no clean table."""


def events_path(name: str) -> Path:
    """Path of the stored CSV file of one source."""
    return DATA_DIR / f"{name}.csv"


def read_events(name: str) -> pd.DataFrame:
    """Load the stored events of one source.

    Args:
        name: Key of `SOURCES`.

    Returns:
        The stored rows. Only empty cells are missing values, so a code such
        as "NA" (Namibia) is kept. Dates stay strings.

    Raises:
        FileNotFoundError: The source has no stored file.
    """
    return read_csv(events_path(name))


def read_all_events() -> pd.DataFrame:
    """Load the stored events of every source as one frame."""
    return pd.concat([read_events(name) for name in SOURCES], ignore_index=True)
