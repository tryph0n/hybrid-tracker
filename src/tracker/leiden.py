"""Russian Operations Against Europe Dataset (Leiden University) - static source.

Pinned to dataset version 4.0 (file RussianOps_12JAN26.xlsx, events 2022-2025).
When a new version is published: update FILE_URL, run
`python -m tracker.ingest leiden` and commit the result. Licence: CC BY 4.0.
https://doi.org/10.7910/DVN/TQ0FMQ
"""

import pandas as pd
import pycountry

from tracker.schema import COLUMNS, make_event_id

FILE_URL = "https://dataverse.harvard.edu/api/access/datafile/13350817"
DATASET_URL = "https://doi.org/10.7910/DVN/TQ0FMQ"
HEADERS = {"User-Agent": "hybrid-tracker (open-data dashboard)"}

# The source's "EventCategory" keeps its exact wording in `subcategory`.
CATEGORY = {
    "Sabotage": "sabotage_attack",
    "Sabotage (preparation)": "sabotage_attack",
    "Sabotage (attempt)": "sabotage_attack",
    "Arson": "sabotage_attack",
    "Terrorism": "sabotage_attack",
    "Assassination": "sabotage_attack",
    "Assassination (plot)": "sabotage_attack",
    "Assassination (attempt)": "sabotage_attack",
    "Influence operation": "influence",
    "Vandalism": "influence",
    "Reconnaissance": "reconnaissance",
    "Disruption": "other",
    "Disruptive immigration": "other",
    "Unknown": "other",
}

# Values of the "Where" column that are not countries.
NOT_A_COUNTRY = {"Baltic Sea", "North Sea", ""}
COUNTRY_ALIASES = {"The Netherlands": "Netherlands"}


def fetch() -> pd.DataFrame:
    """Download the pinned spreadsheet and return it in the unified schema.

    Raises:
        OSError: The download failed.
        KeyError: A column is missing (see `normalize`).
        ValueError: A year or a month name cannot be read, or the sheet
            `EventData` is missing.
        LookupError: A country name is unknown (see `to_iso2`).
    """
    raw = pd.read_excel(FILE_URL, sheet_name="EventData", storage_options=HEADERS)
    return normalize(raw)


def normalize(raw: pd.DataFrame) -> pd.DataFrame:
    """Map the EventData sheet to the unified schema.

    Args:
        raw: The EventData sheet with the columns `IncidentYear`,
            `IncidentMonth`, `Where`, `EventCategory` and `What`. The index
            does not matter: the ids come from the content of the rows
            (see `event_ids`).

    Returns:
        The rows with an `IncidentYear` (the author's notes below the events
        have none and are dropped), with exactly the columns of
        `tracker.schema.COLUMNS`. An unknown `EventCategory` leaves `category`
        empty, which `validate` rejects.

    Raises:
        KeyError: A column of `raw` is missing.
        ValueError: A year or a month name cannot be read.
        LookupError: A country name is unknown.
    """
    raw = raw.dropna(subset=["IncidentYear"])

    # Dates are known to the month only. One event has no month: use January.
    year = raw["IncidentYear"].astype(int).astype(str)
    month = raw["IncidentMonth"].replace("Unknown", "January")
    date = pd.to_datetime(year + " " + month, format="%Y %B")

    events = pd.DataFrame(
        {
            "event_id": event_ids(raw),
            "source": "leiden",
            "date": date.dt.strftime("%Y-%m-%d"),
            "date_precision": "month",
            "countries": raw["Where"].map(to_iso2),
            "place": raw["Where"],
            "lat": float("nan"),
            "lon": float("nan"),
            "category": raw["EventCategory"].map(CATEGORY),
            "subcategory": raw["EventCategory"],
            "title": raw["What"].str.strip(),
            # Every listed event is documented and judged Russian by the author.
            "event_status": "confirmed",
            "russia_link": "assessed",
            "url": DATASET_URL,
        }
    )
    return events[COLUMNS]


def event_ids(raw: pd.DataFrame) -> pd.Series:
    """Build the id of each EventData row from its content.

    The file has no identifier. The key is the raw `IncidentYear` (as an
    integer), the raw `IncidentMonth` text, `What` and `Where`, so a later
    change of the dating rule does not move the ids. Normalisation and
    hashing: see `tracker.schema.make_event_id`.

    Args:
        raw: Rows with the columns `IncidentYear`, `IncidentMonth`, `What`
            and `Where`, all with a year.

    Returns:
        The ids, with the index of `raw`. Two rows with the same key get the
        same id, which `validate` rejects.
    """
    keys = zip(
        raw["IncidentYear"].astype(int).astype(str),
        raw["IncidentMonth"].astype(str),
        raw["What"].astype(str),
        raw["Where"].fillna("").astype(str),
        strict=True,
    )
    return pd.Series(
        [make_event_id("leiden", key=key) for key in keys], index=raw.index
    )


def to_iso2(where: str) -> str:
    """Turn "Poland; Baltic Sea; Germany" into "PL;DE".

    Args:
        where: Country names separated by ";". Names in `NOT_A_COUNTRY` are
            skipped, and `COUNTRY_ALIASES` are applied before the lookup.

    Returns:
        The alpha-2 codes joined by ";", in order, without duplicates. Empty
        when no name is a country.

    Raises:
        LookupError: A name is neither skipped nor a known country.
    """
    codes = []
    for name in where.split(";"):
        name = name.strip()
        if name in NOT_A_COUNTRY:
            continue
        name = COUNTRY_ALIASES.get(name, name)
        code = pycountry.countries.lookup(name).alpha_2
        if code not in codes:
            codes.append(code)
    return ";".join(codes)
