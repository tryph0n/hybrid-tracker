"""GeoNames populated places - reference data, not an event source.

`cities500.zip` lists the populated places with at least 500 inhabitants. The
file is regenerated every day and has no versions, so `tracker ingest geonames`
keeps each download in the raw store with its SHA-256 and its date, and the
build reads the latest stored file. `lookup` turns it into one coordinate pair
per (place name, country), which the build joins to the events that have a
place and no coordinates. Licence: CC BY 4.0. https://www.geonames.org
"""

import csv
import io
import unicodedata
import urllib.request
import zipfile

import pandas as pd

from tracker.errors import UserError
from tracker.store import Payload

SOURCE = "geonames"
FILE_NAME = "cities500.zip"
URL = f"https://download.geonames.org/export/dump/{FILE_NAME}"
HEADERS = {"User-Agent": "hybrid-tracker (open-data dashboard, manual refresh)"}

FIELDS = [
    "geonameid",
    "name",
    "asciiname",
    "alternatenames",
    "latitude",
    "longitude",
    "feature_class",
    "feature_code",
    "country_code",
    "cc2",
    "admin1",
    "admin2",
    "admin3",
    "admin4",
    "population",
    "elevation",
    "dem",
    "timezone",
    "modified",
]
USED = [
    "geonameid",
    "name",
    "asciiname",
    "alternatenames",
    "latitude",
    "longitude",
    "feature_code",
    "country_code",
    "population",
]

EXCLUDED_CODES = {"PPLX", "PPLQ", "PPLH", "PPLW", "PPLCH"}
"""Feature codes that are not a town: a section of a town (PPLX), an abandoned
(PPLQ), historical (PPLH), destroyed (PPLW) or former capital (PPLCH) place."""

CODE_RANK = {
    code: rank
    for rank, code in enumerate(
        ["PPLC", "PPLG", "PPLA", "PPLA2", "PPLA3", "PPLA4", "PPLA5", "PPLR", "PPLL"]
    )
}
"""Order of the feature codes of a place: capital first, then seats of an
administrative division. Any other code (`PPL`, ...) comes last."""

LOOKUP_COLUMNS = ["key", "country", "lat", "lon", "geonameid"]


def extract() -> list[Payload]:
    """Download `cities500.zip` (120 s timeout), as it is served.

    Raises:
        OSError: The download failed.
    """
    request = urllib.request.Request(URL, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=120) as response:
        return [Payload(SOURCE, FILE_NAME, response.read(), URL, response.status)]


def fold(text: str) -> str:
    """Case-fold a name and drop its diacritics, so spellings compare equal.

    The same function folds the names of the file and the places of the events.
    """
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    bare = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(bare.split())


def read_places(archive: bytes) -> pd.DataFrame:
    """Read the tab-separated file inside `cities500.zip`, every field as text.

    Quoting is off, because names hold literal `"` characters, and no text is
    read as a missing value, because the country code of Namibia is `NA`.

    Raises:
        ValueError: The archive does not hold exactly one `.txt` file.
    """
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        members = [name for name in zipped.namelist() if name.endswith(".txt")]
        if len(members) != 1:
            raise UserError(f"Expected one .txt file in the archive, got {members}")
        content = zipped.read(members[0])
    return pd.read_csv(
        io.BytesIO(content),
        sep="\t",
        header=None,
        names=FIELDS,
        usecols=USED,
        dtype=str,
        quoting=csv.QUOTE_NONE,
        keep_default_na=False,
        encoding="utf-8",
    )


def lookup(archive: bytes) -> pd.DataFrame:
    """One place per (folded name, country code), chosen by a fixed rule.

    A place answers to its `name`, its `asciiname` and each of its
    `alternatenames`. When several places answer to the same name in the same
    country, the first one of this order wins, so the same file always gives
    the same coordinates:

    1. a match on `name` or `asciiname` before a match on an alternate name;
    2. the larger population;
    3. the feature code order of `CODE_RANK`;
    4. the smaller `geonameid`.

    Places with a feature code of `EXCLUDED_CODES` are left out.

    Returns:
        Columns `LOOKUP_COLUMNS`, sorted by `key` and `country`.
    """
    places = read_places(archive)
    places = places[~places["feature_code"].isin(EXCLUDED_CODES)].copy()
    places["geonameid"] = places["geonameid"].astype(int)
    places["population"] = pd.to_numeric(places["population"], errors="coerce").fillna(
        0
    )
    places["code_rank"] = places["feature_code"].map(CODE_RANK).fillna(len(CODE_RANK))
    places["lat"] = places["latitude"].astype(float)
    places["lon"] = places["longitude"].astype(float)

    alternates = places["alternatenames"].str.split(",").explode()
    names = pd.concat(
        [
            pd.DataFrame({"text": places["name"], "kind": 0}),
            pd.DataFrame({"text": places["asciiname"], "kind": 0}),
            pd.DataFrame({"text": alternates, "kind": 1}),
        ]
    )
    names["key"] = names["text"].map(fold)
    names = names[names["key"] != ""]
    matches = names.join(
        places[["geonameid", "country_code", "lat", "lon", "population", "code_rank"]]
    ).rename(columns={"country_code": "country"})
    matches = matches.sort_values(
        ["key", "country", "kind", "population", "code_rank", "geonameid"],
        ascending=[True, True, True, False, True, True],
    )
    chosen = matches.drop_duplicates(["key", "country"])
    return chosen[LOOKUP_COLUMNS].reset_index(drop=True)
