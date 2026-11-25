"""Unified event schema shared by every source adapter."""

from pathlib import Path

import pandas as pd

SCHEMA_VERSION = "2.0.0"

# Column order of every file in data/events/.
COLUMNS = [
    "event_id",  # "<source>:<native id>", unique across the whole dataset
    "source",  # key of tracker.sources.SOURCES
    "date",  # event start, YYYY-MM-DD (UTC date when the source gives a time)
    "end_date",  # end of a campaign, YYYY-MM-DD, not before date, empty for a single incident
    "date_precision",  # "day", "approximate" (day marked approximate), "month" (on the 1st)
    "countries",  # ISO 3166-1 alpha-2 codes joined by ";", empty for open sea
    "place",  # free-text location as given by the source
    "lat",  # decimal degrees, empty when the source has no coordinates
    "lon",  # decimal degrees, empty when the source has no coordinates
    "geo_precision",  # "exact" (coordinates), "place" (named place), "country", "sea" (only a sea named)
    "domain",  # one of DOMAINS, the field of the event
    "category",  # one of CATEGORIES, and a category of the domain
    "subcategory",  # the source's own label, untouched
    "title",  # one-line description in English
    "event_status",  # one of EVENT_STATUSES: is the event itself established?
    "actors",  # linked states, ISO 3166-1 alpha-2 codes joined by ";", empty when attribution is none
    "actor_name",  # unit or group, for example "APT28"
    "attribution",  # one of ATTRIBUTIONS: who attributes the event to the actors?
    "attributed_by",  # who made the link between the event and the actors
    "url",  # page with details and references
]

DOMAINS = [
    "air",
    "sabotage",
    "violence",
    "maritime",
    "cyber",
    "information",
    "espionage",
    "other",
]

CATEGORIES = [
    "drone_overflight",
    "airport_disruption",
    "airspace_incursion",
    "airspace_disruption",
    "sabotage",
    "arson",
    "bomb_or_threat",
    "assassination",
    "cable_or_pipeline_damage",
    "vessel_incident",
    "service_disruption",
    "intrusion",
    "data_theft_or_leak",
    "influence_operation",
    "symbolic_vandalism",
    "reconnaissance",
    "instrumentalised_migration",
    "unclassified",
]

# Each category belongs to one domain.
CATEGORY_DOMAINS = {
    "drone_overflight": "air",
    "airport_disruption": "air",
    "airspace_incursion": "air",
    "airspace_disruption": "air",
    "sabotage": "sabotage",
    "arson": "sabotage",
    "bomb_or_threat": "violence",
    "assassination": "violence",
    "cable_or_pipeline_damage": "maritime",
    "vessel_incident": "maritime",
    "service_disruption": "cyber",
    "intrusion": "cyber",
    "data_theft_or_leak": "cyber",
    "influence_operation": "information",
    "symbolic_vandalism": "information",
    "reconnaissance": "espionage",
    "instrumentalised_migration": "other",
    "unclassified": "other",
}

# "dismissed": the source itself concluded there was no event (e.g. no drone).
EVENT_STATUSES = ["confirmed", "unconfirmed", "dismissed"]

# "official": a government, prosecutor, court or intelligence service named the state.
# "assessed": the source's analysts or a security vendor judge the state responsible.
# "claimed": a group claimed the act and declares its allegiance.
# "none": no source attributes the event to a state.
ATTRIBUTIONS = ["official", "assessed", "claimed", "none"]

DATE_PRECISIONS = ["day", "approximate", "month"]

GEO_PRECISIONS = ["exact", "place", "country", "sea"]

REQUIRED = [
    "event_id",
    "source",
    "date",
    "date_precision",
    "geo_precision",
    "domain",
    "category",
    "title",
    "event_status",
    "attribution",
    "url",
]

COUNTRIES_PATTERN = r"[A-Z]{2}(;[A-Z]{2})*"

ACTORS_PATTERN = COUNTRIES_PATTERN

ALLOWED_VALUES = {
    "date_precision": DATE_PRECISIONS,
    "geo_precision": GEO_PRECISIONS,
    "domain": DOMAINS,
    "category": CATEGORIES,
    "event_status": EVENT_STATUSES,
    "attribution": ATTRIBUTIONS,
}


def validate(events: pd.DataFrame) -> None:
    """Raise ValueError if `events` does not follow the schema.

    Checks, in order: the columns and their order are exactly `COLUMNS`; there
    is at least one row; no `REQUIRED` cell is missing (NaN, None, or an empty
    or whitespace-only string); `event_id` is unique; `ALLOWED_VALUES`
    columns hold allowed values; each `category` belongs to the row's
    `domain`; non-empty `countries` and `actors` match `COUNTRIES_PATTERN`
    and `ACTORS_PATTERN`; `actors` is empty exactly when `attribution` is
    "none"; `date` parses with `pd.to_datetime` and `%Y-%m-%d` (so "2025-1-5"
    passes); a non-empty `end_date` parses the same way and is not before
    `date`; `lat` and `lon` are both set or both missing, and in range when
    set.

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

    mismatched = events[events["category"].map(CATEGORY_DOMAINS) != events["domain"]]
    if not mismatched.empty:
        pairs = sorted(set(zip(mismatched["category"], mismatched["domain"])))
        raise ValueError(f"category not in domain: {pairs}")

    for column, pattern in [
        ("countries", COUNTRIES_PATTERN),
        ("actors", ACTORS_PATTERN),
    ]:
        values = events[column].dropna().astype(str)
        bad = values[(values != "") & ~values.str.fullmatch(pattern)]
        if not bad.empty:
            raise ValueError(f"Invalid {column} values: {sorted(set(bad))}")

    has_actors = events["actors"].astype("string").fillna("") != ""
    is_none = events["attribution"] == "none"
    if (~is_none & ~has_actors).any():
        raise ValueError("attribution other than none needs actors")
    if (is_none & has_actors).any():
        raise ValueError("attribution none needs empty actors")

    # Raises ValueError on a date that does not parse with %Y-%m-%d.
    start = pd.to_datetime(events["date"], format="%Y-%m-%d")
    end_text = events["end_date"].astype("string").fillna("")
    has_end = end_text != ""
    end = pd.to_datetime(end_text[has_end], format="%Y-%m-%d")
    if (end < start[has_end]).any():
        raise ValueError("end_date before date")

    if (events["lat"].isna() != events["lon"].isna()).any():
        raise ValueError("lat and lon must be both set or both empty")
    if not events["lat"].dropna().between(-90, 90).all():
        raise ValueError("lat out of range")
    if not events["lon"].dropna().between(-180, 180).all():
        raise ValueError("lon out of range")


def read_csv(
    source: str | Path, storage_options: dict[str, str] | None = None
) -> pd.DataFrame:
    """Read a CSV where only an empty cell is a missing value.

    Texts such as "NA" (Namibia) or "None" are real values, so pandas'
    default list of missing-value texts is switched off.
    """
    return pd.read_csv(
        source,
        keep_default_na=False,
        na_values=[""],
        storage_options=storage_options,
    )
