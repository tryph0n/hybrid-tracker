"""Unified event schema shared by every source adapter."""

import pandas as pd

# Column order of every file in data/events/.
COLUMNS = [
    "event_id",  # "<source>:<native id>", unique across the whole dataset
    "source",  # key of tracker.sources.SOURCES
    "date",  # event start, YYYY-MM-DD (UTC date when the source gives a time)
    "date_precision",  # "day", "approximate" (day marked approximate), "month" (on the 1st)
    "countries",  # ISO 3166-1 alpha-2 codes joined by ";", empty for open sea
    "place",  # free-text location as given by the source
    "lat",  # decimal degrees, empty when the source has no coordinates
    "lon",  # decimal degrees, empty when the source has no coordinates
    "category",  # one of CATEGORIES
    "subcategory",  # the source's own label, untouched
    "title",  # one-line description in English
    "event_status",  # one of EVENT_STATUSES: is the event itself established?
    "russia_link",  # one of RUSSIA_LINKS: who links the event to Russia?
    "url",  # page with details and references
]

CATEGORIES = [
    "drone_overflight",
    "airport_disruption",
    "airspace_incursion",
    "sabotage_attack",
    "influence",
    "reconnaissance",
    "other",
]

# "dismissed": the source itself concluded there was no event (e.g. no drone).
EVENT_STATUSES = ["confirmed", "unconfirmed", "dismissed"]

# "official": a public authority named Russia.
# "assessed": the source's analysts judge Russia responsible.
# "none": nobody attributed the event to Russia.
RUSSIA_LINKS = ["official", "assessed", "none"]

DATE_PRECISIONS = ["day", "approximate", "month"]

REQUIRED = [
    "event_id",
    "source",
    "date",
    "date_precision",
    "category",
    "title",
    "event_status",
    "russia_link",
    "url",
]

COUNTRIES_PATTERN = r"[A-Z]{2}(;[A-Z]{2})*"

ALLOWED_VALUES = {
    "category": CATEGORIES,
    "event_status": EVENT_STATUSES,
    "russia_link": RUSSIA_LINKS,
    "date_precision": DATE_PRECISIONS,
}


def validate(events: pd.DataFrame) -> None:
    """Raise ValueError if `events` does not follow the schema.

    Checks, in order: the columns and their order are exactly `COLUMNS`; there
    is at least one row; no `REQUIRED` cell is missing (NaN, None, or an empty
    or whitespace-only string); `event_id` is unique; `ALLOWED_VALUES`
    columns hold allowed values; non-empty `countries` match
    `COUNTRIES_PATTERN`; `date` parses with `pd.to_datetime` and `%Y-%m-%d`
    (so "2025-1-5" passes); `lat` and `lon` are both set or both missing, and
    in range when set.

    Args:
        events: The events of one source or of all sources.

    Raises:
        ValueError: A check fails.
    """
    if list(events.columns) != COLUMNS:
        raise ValueError(f"Unexpected columns: {list(events.columns)}")
    if events.empty:
        raise ValueError("No events")

    for column in REQUIRED:
        blank = events[column].astype(str).str.strip() == ""
        missing = (events[column].isna() | blank).sum()
        if missing:
            raise ValueError(f"{missing} rows without {column}")

    if not events["event_id"].is_unique:
        raise ValueError("Duplicate event_id")

    for column, allowed in ALLOWED_VALUES.items():
        unknown = set(events[column]) - set(allowed)
        if unknown:
            raise ValueError(f"Unknown {column} values: {sorted(unknown)}")

    countries = events["countries"].dropna().astype(str)
    bad_countries = countries[
        (countries != "") & ~countries.str.fullmatch(COUNTRIES_PATTERN)
    ]
    if not bad_countries.empty:
        raise ValueError(f"Invalid countries values: {sorted(set(bad_countries))}")

    # Raises ValueError on a date that does not parse with %Y-%m-%d.
    pd.to_datetime(events["date"], format="%Y-%m-%d")

    if (events["lat"].isna() != events["lon"].isna()).any():
        raise ValueError("lat and lon must be both set or both empty")
    if not events["lat"].dropna().between(-90, 90).all():
        raise ValueError("lat out of range")
    if not events["lon"].dropna().between(-180, 180).all():
        raise ValueError("lon out of range")
