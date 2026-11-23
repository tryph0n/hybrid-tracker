"""Russian Operations Against Europe Dataset (Leiden University) - static source.

Pinned to dataset version 4.0 (file RussianOps_12JAN26.xlsx, events 2022-2025).
When a new version is published: update FILE_URL, run
`python -m tracker.ingest leiden` and commit the result. Licence: CC BY 4.0.
https://doi.org/10.7910/DVN/TQ0FMQ
"""

import pandas as pd
import pycountry

from tracker.schema import COLUMNS

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
    """Download the pinned spreadsheet and return it in the unified schema."""
    raw = pd.read_excel(FILE_URL, sheet_name="EventData", storage_options=HEADERS)
    return normalize(raw)


def normalize(raw: pd.DataFrame) -> pd.DataFrame:
    """Map the EventData sheet to the unified schema."""
    # Rows below the events hold the author's notes and have no year.
    raw = raw.dropna(subset=["IncidentYear"])

    # Dates are known to the month only. One event has no month: use January.
    year = raw["IncidentYear"].astype(int).astype(str)
    month = raw["IncidentMonth"].replace("Unknown", "January")
    date = pd.to_datetime(year + " " + month, format="%Y %B")

    # The file has no identifier: use the spreadsheet row (header is row 1).
    row_number = (raw.index + 2).astype(str).str.zfill(3)

    events = pd.DataFrame(
        {
            "event_id": "leiden:" + row_number,
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


def to_iso2(where: str) -> str:
    """Turn "Poland; Baltic Sea; Germany" into "PL;DE"."""
    codes = []
    for name in where.split(";"):
        name = name.strip()
        if name in NOT_A_COUNTRY:
            continue
        name = COUNTRY_ALIASES.get(name, name)
        # Raises LookupError on an unknown country name.
        code = pycountry.countries.lookup(name).alpha_2
        if code not in codes:
            codes.append(code)
    return ";".join(codes)
