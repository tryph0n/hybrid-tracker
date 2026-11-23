"""Registry of data sources and access to their stored events."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from tracker import eodi, leiden

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "events"


@dataclass(frozen=True)
class Source:
    title: str
    # "static": frozen historical snapshot, re-ingested by hand.
    # "live": refreshed by the scheduled pipeline.
    kind: str
    homepage: str
    licence: str
    citation: str
    fetch: Callable[[], pd.DataFrame]


SOURCES = {
    "eodi": Source(
        title="European Observatory of Drone Incidents",
        kind="live",
        homepage="https://droneobservatory.eu",
        licence="CC BY 4.0",
        citation=(
            "Lucas Alaniz Pintos, European Observatory of Drone Incidents "
            "(EODI): drone incidents in Europe."
        ),
        fetch=eodi.fetch,
    ),
    "leiden": Source(
        title="Russian Operations Against Europe Dataset, 2022-2025",
        kind="static",
        homepage=leiden.DATASET_URL,
        licence="CC BY 4.0",
        citation=(
            "Bart Schuurman, Russian Operations Against Europe Dataset, "
            "2022-2025, Harvard Dataverse, V4."
        ),
        fetch=leiden.fetch,
    ),
}


def events_path(name: str) -> Path:
    return DATA_DIR / f"{name}.csv"


def read_events(name: str) -> pd.DataFrame:
    """Load the stored events of one source."""
    # Only empty cells are missing values: keeps codes such as "NA" (Namibia).
    return pd.read_csv(events_path(name), keep_default_na=False, na_values=[""])


def read_all_events() -> pd.DataFrame:
    """Load the stored events of every source as one frame."""
    return pd.concat([read_events(name) for name in SOURCES], ignore_index=True)
