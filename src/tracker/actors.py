"""Threat-group lookup from the MISP galaxy threat-actor cluster.

Sources name one threat group in many ways ("Fancy Bear", "APT28", "Sofacy").
`tracker ingest misp_galaxy` keeps the cluster file of a pinned commit in the raw
store. `reduce` turns it into one table, `threat_actors`: one row per group,
with its aliases, its country of origin and the suspected state sponsor when the
cluster gives one. `curation/actors.csv` adds a country to the groups that the
cluster leaves without one. `Lookup` maps a name to one group.

The country is the origin of the group, not proof of state sponsorship:
criminal groups such as TA505 carry `RU`. The lookup proposes a state and never
sets `attribution`. Licence: CC0 1.0 or BSD 2-clause, used here as CC0 1.0.
https://github.com/MISP/misp-galaxy
"""

import csv
import hashlib
import json
import logging
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import pycountry

from tracker.errors import UserError
from tracker.store import Payload

logger = logging.getLogger(__name__)

SOURCE = "misp_galaxy"
FILE_NAME = "threat-actor.json"
COMMIT = "58c8ca52eff025da2048c02938c0c448df219df3"
"""Commit of MISP/misp-galaxy, the last one on the file on 2026-11-23."""

URL = (
    f"https://raw.githubusercontent.com/MISP/misp-galaxy/{COMMIT}/clusters/{FILE_NAME}"
)
SHA256 = "5bffee604295db1dbf811f00628d0a668b11797cd0e123d720011a477ed050ad"
"""Digest of the file at `COMMIT`. A download with another digest is rejected."""

HEADERS = {"User-Agent": "hybrid-tracker (open-data dashboard, manual refresh)"}

COLUMNS = [
    "actor",
    "uuid",
    "aliases",
    "country",
    "state_sponsor",
    "country_source",
    "country_url",
    "country_publisher",
    "country_quote",
    "stance_url",
    "stance_publisher",
    "stance_quote",
]
"""Columns of the `threat_actors` table."""

CURATION_COLUMNS = [
    "actor",
    "country",
    "country_url",
    "country_publisher",
    "country_quote",
    "stance_url",
    "stance_publisher",
    "stance_quote",
]
"""Columns of `curation/actors.csv`. The six last ones are all filled for a
curated country and all empty for a group with no country."""

FROM_CLUSTER = "misp-galaxy"
FROM_CURATION = "curation"

SPONSOR_COUNTRIES = {
    "Unknown": None,
    "United Arab Emirates": "AE",
    "Belarus": "BY",
    "China": "CN",
    "People's Republic of China": "CN",
    "Spain": "ES",
    "France": "FR",
    "Israel": "IL",
    "India": "IN",
    "Iran (Islamic Republic of)": "IR",
    "Korea (Democratic People's Republic of)": "KP",
    "Korea (Republic of)": "KR",
    "Lebanon": "LB",
    "Pakistan": "PK",
    "Palestine": "PS",
    "Russian Federation": "RU",
    "US": "US",
    "United States": "US",
    "Vietnam": "VN",
}
"""The `cfr-suspected-state-sponsor` spellings of the pinned file, as ISO alpha-2.
A new spelling stops the reduction: add it here after a check."""


def extract() -> list[Payload]:
    """Download the cluster file at `COMMIT` (60 s timeout), as it is served.

    Raises:
        OSError: The download failed.
        UserError: The file does not have the digest `SHA256`.
    """
    request = urllib.request.Request(URL, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=60) as response:
        content = response.read()
        status = response.status
    digest = hashlib.sha256(content).hexdigest()
    if digest != SHA256:
        raise UserError(f"{FILE_NAME} at {COMMIT} has sha256 {digest}, not {SHA256}")
    return [Payload(SOURCE, FILE_NAME, content, URL, status)]


def fold(name: str) -> str:
    """Case-fold a name and collapse its spaces, so `FANCY BEAR` equals `Fancy Bear`."""
    return " ".join(name.casefold().split())


def alpha2(code: str, where: str) -> str:
    """The code if it is an ISO 3166-1 alpha-2 code in upper case.

    Raises:
        UserError: It is not.
    """
    if code != code.upper() or pycountry.countries.get(alpha_2=code) is None:
        raise UserError(f"{where}: {code!r} is not an ISO alpha-2 code")
    return code


@dataclass(frozen=True)
class Curation:
    """A line of `curation/actors.csv`.

    `country_*` is the page that links the group to the country, with the exact
    words of the page that support it (the reserve). `stance_*` is the page that
    calls the group pro-Russian, with its exact words.
    """

    actor: str
    country: str | None = None
    country_url: str | None = None
    country_publisher: str | None = None
    country_quote: str | None = None
    stance_url: str | None = None
    stance_publisher: str | None = None
    stance_quote: str | None = None

    def sources(self) -> dict[str, str | None]:
        """The six source fields, by column name."""
        return {
            "country_url": self.country_url,
            "country_publisher": self.country_publisher,
            "country_quote": self.country_quote,
            "stance_url": self.stance_url,
            "stance_publisher": self.stance_publisher,
            "stance_quote": self.stance_quote,
        }


def check_sources(line: Curation, where: str) -> None:
    """A curated country has its six source fields, a group with none has no one.

    Raises:
        UserError: A field is missing or filled against that rule, or a URL is
            not http(s).
    """
    fields = line.sources()
    if line.country is None:
        filled = [name for name, value in fields.items() if value]
        if filled:
            raise UserError(f"{where}: no country, but {', '.join(filled)} is filled")
        return
    missing = [name for name, value in fields.items() if not value]
    if missing:
        raise UserError(f"{where}: a country needs {', '.join(missing)}")
    for name in ("country_url", "stance_url"):
        if not str(fields[name]).startswith(("http://", "https://")):
            raise UserError(f"{where}: {name} must be an http(s) URL")


def read_curation(path: Path) -> list[Curation]:
    """Read `curation/actors.csv`: the columns `CURATION_COLUMNS`.

    The country is where the group is based. `country_url`, `country_quote` and
    `country_publisher` give the public page that says so and its exact words;
    the `stance_*` columns give the page that calls the group pro-Russian. A
    group with no such page keeps an empty country and empty sources.

    Raises:
        UserError: A column is missing, the actor is empty, a country is not
            an alpha-2 code, the source fields break the rule of
            `check_sources`, or a group is listed twice.
    """
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != CURATION_COLUMNS:
            raise UserError(
                f"{path.name}: columns must be {', '.join(CURATION_COLUMNS)}"
            )
        rows = list(reader)
    lines: list[Curation] = []
    seen: set[str] = set()
    for number, row in enumerate(rows, start=2):
        where = f"{path.name} line {number}"
        values = {name: (row[name] or "").strip() or None for name in CURATION_COLUMNS}
        actor = values.pop("actor")
        if not actor:
            raise UserError(f"{where}: an actor is needed")
        if fold(actor) in seen:
            raise UserError(f"{where}: {actor!r} is listed twice")
        seen.add(fold(actor))
        country = values.pop("country")
        line = Curation(actor, alpha2(country, where) if country else None, **values)
        check_sources(line, where)
        lines.append(line)
    return lines


def entry_row(entry: dict[str, Any]) -> dict[str, Any]:
    """One table row from one `values` entry of the cluster."""
    name = entry["value"]
    meta = entry.get("meta", {})
    aliases: list[str] = []
    seen = {fold(name)}
    for alias in meta.get("synonyms", []):
        if fold(alias) and fold(alias) not in seen:
            seen.add(fold(alias))
            aliases.append(alias)
    country = meta.get("country") or None
    if country is not None:
        country = alpha2(country, f"country of {name}")
    sponsor = meta.get("cfr-suspected-state-sponsor")
    if sponsor is not None and sponsor not in SPONSOR_COUNTRIES:
        raise UserError(f"sponsor of {name}: unknown spelling {sponsor!r}")
    return {
        "actor": name,
        "uuid": entry.get("uuid"),
        "aliases": aliases,
        "country": country,
        "state_sponsor": SPONSOR_COUNTRIES[sponsor] if sponsor is not None else None,
        "country_source": FROM_CLUSTER if country else None,
        "country_url": None,
        "country_publisher": None,
        "country_quote": None,
        "stance_url": None,
        "stance_publisher": None,
        "stance_quote": None,
    }


def reduce(content: bytes, curation: list[Curation]) -> pd.DataFrame:
    """Reduce the cluster file to the `threat_actors` table, with no network access.

    A line of `curation` gives a country to the group of that name when the
    cluster has the group with no country, and adds the group when the cluster
    does not have it.

    Returns:
        Columns `COLUMNS`, one row per group, sorted by `actor`.

    Raises:
        UserError: The file has no `values`, a group name is not unique, a
            country is not valid, a curated country lacks one of its source
            fields, or a curation line names a group that the cluster already
            gives a country.
    """
    values = json.loads(content.decode("utf-8")).get("values")
    if not values:
        raise UserError(f"{FILE_NAME}: no values")
    rows = [entry_row(entry) for entry in values]
    names = [row["actor"] for row in rows]
    if len(set(names)) != len(names):
        raise UserError(f"{FILE_NAME}: a group name is not unique")
    by_name = {fold(row["actor"]): row for row in rows}
    for line in curation:
        check_sources(line, line.actor)
        row = by_name.get(fold(line.actor))
        if row is None:
            row = entry_row({"value": line.actor})
            rows.append(row)
        if line.country is None:
            continue
        if row["country"] is not None:
            raise UserError(
                f"{line.actor}: the cluster already gives the country {row['country']}"
            )
        row["country"] = line.country
        row["country_source"] = FROM_CURATION
        row.update(line.sources())
    frame = pd.DataFrame(rows, columns=COLUMNS)
    return frame.sort_values("actor", ignore_index=True)


class Lookup:
    """Turns a group name into one group, and a group into a proposed state."""

    def __init__(self, table: pd.DataFrame) -> None:
        self.table = table.set_index("actor")
        index: dict[str, set[str]] = defaultdict(set)
        for actor, aliases in zip(table["actor"], table["aliases"], strict=True):
            for name in [actor, *aliases]:
                index[fold(name)].add(actor)
        self.index = {name: frozenset(actors) for name, actors in index.items()}
        """Folded name to the set of groups that carry it."""

    def groups(self, name: str) -> frozenset[str]:
        """The groups that carry `name`, without case. Empty if none does."""
        return self.index.get(fold(name), frozenset())

    def resolve(self, name: str) -> str | None:
        """The canonical name of the group, or None.

        A name that two groups carry resolves to nothing, not to a guess.
        """
        found = self.groups(name)
        return next(iter(found)) if len(found) == 1 else None

    def state(self, name: str) -> str | None:
        """The state that the group of `name` proposes, as an ISO alpha-2 code.

        It is the country of origin of the group, or the suspected state
        sponsor when the group has no country. It is a proposal: the caller
        never sets `attribution` from it.
        """
        actor = self.resolve(name)
        if actor is None:
            return None
        row = self.table.loc[actor]
        for column in ("country", "state_sponsor"):
            if pd.notna(row[column]):
                return str(row[column])
        return None
