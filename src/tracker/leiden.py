"""Russian Operations Against Europe Dataset (Leiden University) - static source.

Pinned to dataset version 4.0 (events 2022-2025). `extract` lists the files of
the latest published version on Harvard Dataverse and stops when the licence
is not CC BY 4.0 or when the version is not `PINNED_VERSION`, so a new
version never enters the data without a review. When a new version is
published: check its licence, read its sheet for new labels, set
`PINNED_VERSION`, run `tracker ingest leiden`, read the diff of the
CSV and update the citation (see docs/maintainer.md). Licence: CC BY 4.0.
https://doi.org/10.7910/DVN/TQ0FMQ
"""

import io
import json
import urllib.parse
import urllib.request
import warnings
from collections.abc import Mapping
from typing import Any

import openpyxl
import pandas as pd
import pycountry

from tracker.errors import UserError
from tracker.schema import COLUMNS, make_event_id
from tracker.store import Payload

DATASET_URL = "https://doi.org/10.7910/DVN/TQ0FMQ"
API = "https://dataverse.harvard.edu/api"
PERSISTENT_ID = "persistentId=doi:10.7910/DVN/TQ0FMQ"
VERSION_URL = f"{API}/datasets/:persistentId/versions/:latest?{PERSISTENT_ID}"
FILES_URL = (
    f"{API}/datasets/:persistentId/versions/:latest-published/files?{PERSISTENT_ID}"
)
FILE_URL = f"{API}/access/datafile/{{id}}"
HEADERS = {"User-Agent": "hybrid-tracker (open-data dashboard)"}
WORKBOOK_NAME = "eventdata.xlsx"
VERSION_NAME = "version.json"
PINNED_VERSION = "4.0"
LICENCE = "CC BY 4.0"
ATTRIBUTED_BY = "Leiden University dataset (B. Schuurman)"

# The source's "EventCategory" keeps its exact wording in `subcategory`.
# Each label maps to (domain, category) of schema v2 (record 0002).
CATEGORY = {
    "Sabotage": ("sabotage", "sabotage"),
    "Sabotage (preparation)": ("sabotage", "sabotage"),
    "Sabotage (attempt)": ("sabotage", "sabotage"),
    "Arson": ("sabotage", "arson"),
    "Terrorism": ("violence", "bomb_or_threat"),
    "Assassination": ("violence", "assassination"),
    "Assassination (plot)": ("violence", "assassination"),
    "Assassination (attempt)": ("violence", "assassination"),
    "Influence operation": ("information", "influence_operation"),
    "Vandalism": ("information", "symbolic_vandalism"),
    "Reconnaissance": ("espionage", "reconnaissance"),
    "Disruption": ("air", "airspace_disruption"),
    "Disruptive immigration": ("other", "instrumentalised_migration"),
    "Unknown": ("other", "unclassified"),
}

# Values of the "Where" column that are not countries.
NOT_A_COUNTRY = {"Baltic Sea", "North Sea", ""}
COUNTRY_ALIASES = {"The Netherlands": "Netherlands"}

# Texts of "IncidentDateEnd" that are not a date. "DNA" means that the author
# gives no end. The month names have no year, so they give no end date either.
# Any other text stops the run.
NO_END_DATE = {"DNA", "July", "May"}


def get_bytes(url: str) -> bytes:
    """Read an answer of the Dataverse API, as it is served (60 s timeout)."""
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=60) as response:
        content: bytes = response.read()
    return content


def get_json(url: str) -> dict[str, Any]:
    """Read a JSON answer of the Dataverse API (60 s timeout)."""
    answer: dict[str, Any] = json.loads(get_bytes(url))
    return answer


def check_version(answer: dict[str, Any]) -> None:
    """Stop unless the latest version is CC BY 4.0 and `PINNED_VERSION`.

    Args:
        answer: The JSON answer of `VERSION_URL`.

    Raises:
        ValueError: The licence is not `LICENCE` (versions 1 to 3 were CC
            BY-NC-SA 4.0, which the stored data cannot use), or the version
            is not `PINNED_VERSION`.
    """
    data = answer["data"]
    licence = data["license"]["name"]
    if licence != LICENCE:
        raise UserError(f"The licence is {licence!r}, not {LICENCE!r}")
    version = f"{data['versionNumber']}.{data['versionMinorNumber']}"
    if version != PINNED_VERSION:
        raise UserError(
            f"Version {version} is published, the adapter is pinned to "
            f"{PINNED_VERSION}: review it, then change PINNED_VERSION and the "
            "citation"
        )


def spreadsheet_id(answer: dict[str, Any]) -> int:
    """Return the file id of the only .xlsx file of a version.

    Args:
        answer: The JSON answer of `FILES_URL`.

    Raises:
        ValueError: The version has no .xlsx file or more than one.
    """
    ids = [
        item["dataFile"]["id"]
        for item in answer["data"]
        if item["dataFile"]["filename"].lower().endswith(".xlsx")
    ]
    if len(ids) != 1:
        raise UserError(f"Expected one .xlsx file, found {len(ids)}")
    return int(ids[0])


def extract() -> list[Payload]:
    """Download the latest spreadsheet and the version answer, as they are served.

    The version and the licence are checked first, so the payload of a new
    version is never stored without a review. The version answer is kept as a
    second payload: it holds the release time (see `updated_by_source`).

    Returns:
        Two payloads, named `WORKBOOK_NAME` and `VERSION_NAME`.

    Raises:
        OSError: A request failed.
        KeyError: A field of an API answer is missing (see `check_version`).
        ValueError: The licence or the version is not the expected one, or the
            version has not exactly one .xlsx file.
    """
    version = get_bytes(VERSION_URL)
    check_version(json.loads(version))
    file_id = spreadsheet_id(get_json(FILES_URL))
    url = FILE_URL.format(id=file_id)
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=60) as response:
        workbook = Payload(
            "leiden", WORKBOOK_NAME, response.read(), url, response.status
        )
    return [workbook, Payload("leiden", VERSION_NAME, version, VERSION_URL, 200)]


def updated_by_source(files: Mapping[str, bytes]) -> str | None:
    """The `releaseTime` of the stored version answer.

    Args:
        files: The bytes of the payloads, by name.

    Returns:
        The release time as the API gives it (UTC, for example
        `2026-01-13T14:22:43Z`), or None when the answer is not stored (a
        store filled before this payload existed) or has no release time.
    """
    if VERSION_NAME not in files:
        return None
    release: str | None = json.loads(files[VERSION_NAME])["data"].get("releaseTime")
    return release


def normalize(files: Mapping[str, bytes]) -> pd.DataFrame:
    """Map the stored spreadsheet to the unified schema.

    Reads nothing but `files`: no network.

    Args:
        files: The bytes of the payloads, by name: `WORKBOOK_NAME`.

    Raises:
        KeyError: The payload or the sheet `EventData` is missing, or a column
            is missing (see `map_frame`).
        ValueError: A year, a month name or an end date cannot be read.
        LookupError: A country name is unknown (see `to_iso2`).
    """
    return map_frame(read_workbook(io.BytesIO(files[WORKBOOK_NAME])))


def read_workbook(book: io.BytesIO | str) -> pd.DataFrame:
    """Read the EventData sheet, with the link of each "What" cell.

    `pandas.read_excel` drops hyperlinks, so the sheet is read with openpyxl.

    Args:
        book: A path or a file object of the .xlsx file.

    Returns:
        One row per sheet row, with the sheet's header as columns and one
        more column, `WhatUrl`: the target of the hyperlink of the "What"
        cell, or None.

    Raises:
        KeyError: The sheet `EventData` is missing.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        sheet = openpyxl.load_workbook(book)["EventData"]
    rows = list(sheet.iter_rows())
    header = [cell.value for cell in rows[0]]
    what = header.index("What")
    frame = pd.DataFrame(
        [[cell.value for cell in row] for row in rows[1:]], columns=header
    )
    frame["WhatUrl"] = [
        row[what].hyperlink.target if row[what].hyperlink else None for row in rows[1:]
    ]
    return frame


def map_frame(raw: pd.DataFrame) -> pd.DataFrame:
    """Map the EventData sheet to the unified schema.

    Args:
        raw: The sheet as `read_workbook` returns it, with the columns
            `IncidentYear`, `IncidentMonth`, `IncidentDateEnd`, `Where`,
            `EventCategory`, `What` and `WhatUrl`. The index does not matter:
            the ids come from the content of the rows (see `event_ids`).

    Returns:
        The rows with an `IncidentYear` (the author's notes below the events
        have none and are dropped), with exactly the columns of
        `tracker.schema.COLUMNS`. An unknown `EventCategory` leaves `domain`
        and `category` empty, which `validate` rejects. `url` is the "What"
        hyperlink, or the dataset page when the row has none. `geo_precision`
        is `sea` when no country is named, else `country`.

    Raises:
        KeyError: A column of `raw` is missing.
        ValueError: A year, a month name or an end date cannot be read.
        LookupError: A country name is unknown.
    """
    raw = raw.dropna(subset=["IncidentYear"])

    # A date is known to the month, or to the year when the month is "Unknown":
    # that row is dated 1 January.
    year = raw["IncidentYear"].astype(int).astype(str)
    year_only = raw["IncidentMonth"] == "Unknown"
    month = raw["IncidentMonth"].replace("Unknown", "January")
    date = pd.to_datetime(year + " " + month, format="%Y %B")

    countries = raw["Where"].map(to_iso2)
    link = raw["WhatUrl"].fillna("").astype(str).map(strip_tracking)
    mapped = raw["EventCategory"].map(CATEGORY)

    events = pd.DataFrame(
        {
            "event_id": event_ids(raw),
            "source": "leiden",
            "date": date.dt.strftime("%Y-%m-%d"),
            "end_date": end_dates(raw["IncidentDateEnd"]),
            "date_precision": year_only.map({True: "year", False: "month"}),
            "countries": countries,
            "place": raw["Where"],
            "lat": float("nan"),
            "lon": float("nan"),
            "geo_precision": countries.map(lambda c: "country" if c else "sea"),
            "domain": mapped.map(
                lambda pair: pair[0] if isinstance(pair, tuple) else None
            ),
            "category": mapped.map(
                lambda pair: pair[1] if isinstance(pair, tuple) else None
            ),
            "subcategory": raw["EventCategory"],
            "title": raw["What"].str.strip(),
            # Every listed event is documented and judged Russian by the author.
            "event_status": "confirmed",
            "drone_presence": None,
            "actors": "RU",
            "actor_name": None,
            "attribution": "assessed",
            "attributed_by": ATTRIBUTED_BY,
            "url": link.where(link != "", DATASET_URL),
        }
    )
    return events[COLUMNS]


def strip_tracking(url: str) -> str:
    """Remove the `utm_*` tracking parameters of a link and strip the text.

    The author's links carry them (for example `?utm_source=...`). They name
    the tool that opened the page, not the page.
    """
    url = url.strip()
    parts = urllib.parse.urlsplit(url)
    pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query = [pair for pair in pairs if not pair[0].startswith("utm_")]
    if len(query) == len(pairs):
        return url
    return urllib.parse.urlunsplit(
        parts._replace(query=urllib.parse.urlencode(query, safe="/:"))
    )


def end_dates(values: pd.Series) -> pd.Series:
    """Turn the "IncidentDateEnd" cells into ISO dates or empty text.

    Args:
        values: Cells that hold a date, a text of `NO_END_DATE` or nothing.

    Returns:
        "YYYY-MM-DD" for a date, "" for a text of `NO_END_DATE` or an empty
        cell.

    Raises:
        ValueError: A cell holds another text.
    """

    def convert(value: Any) -> str:
        if pd.isna(value):
            return ""
        if isinstance(value, str):
            if value.strip() not in NO_END_DATE:
                raise UserError(f"Unknown IncidentDateEnd text: {value!r}")
            return ""
        return pd.Timestamp(value).strftime("%Y-%m-%d")

    return values.map(convert)


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
        raw["What"].fillna("").astype(str),
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
