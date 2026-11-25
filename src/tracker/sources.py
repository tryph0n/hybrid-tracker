"""Registry of data sources and access to their stored events."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from tracker import eodi, leiden
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


FAMILIES = ("open", "nc")


SOURCES = {
    "eodi": Source(
        title="European Observatory of Drone Incidents",
        kind="live",
        homepage="https://droneobservatory.eu",
        licence="CC BY 4.0",
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
        family="open",
        citation=(
            "Bart Schuurman, Russian Operations Against Europe Dataset, "
            "2022-2025, Harvard Dataverse, V4."
        ),
        extract=leiden.extract,
        normalize=leiden.normalize,
    ),
}


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
