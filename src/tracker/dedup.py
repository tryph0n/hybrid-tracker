"""Clusters of the rows that two sources record for one incident.

The rules are in `docs/decisions/0004-deduplication.md`. `assign_clusters`
adds `cluster_id`, `n_sources` and `is_canonical` to a table of events. It
compares the pairs of rows from different sources: a pair is a candidate when
the rows share a country and their dates match, and it matches when the rows
are less than `DISTANCE_KM` apart, or when the place and the titles are
similar. The merges and the splits of the curation files correct the matched
pairs. A cluster holds at most one row of each source.
"""

import csv
import re
import unicodedata
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

import pandas as pd
import pycountry
from rapidfuzz import fuzz

from tracker.errors import UserError

DISTANCE_KM = 25.0
WORD_THRESHOLD = 75.0
TITLE_THRESHOLD = 40.0
EARTH_RADIUS_KM = 6371.0088

CURATION_COLUMNS = ["event_id_a", "event_id_b", "reason"]

FOLDED = str.maketrans(
    {
        "ø": "o",
        "æ": "ae",
        "œ": "oe",
        "ß": "ss",
        "ł": "l",
        "đ": "d",
        "þ": "th",
        "ð": "d",
        "ı": "i",
    }
)

Pair = tuple[str, str]


@dataclass(frozen=True)
class Match:
    """A pair of rows that the match rule accepts, with what orders the pairs."""

    first: str
    second: str
    score: float
    gap: int


def words(text: object) -> list[str]:
    """The words of a text: case-folded, without accents, a to z and digits only."""
    if not isinstance(text, str):
        return []
    folded = text.casefold().translate(FOLDED)
    decomposed = unicodedata.normalize("NFKD", folded)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.findall(r"[a-z0-9]+", stripped)


def period_bounds(value: str, precision: str) -> tuple[date, date]:
    """First and last day of the period that a date stands for, by its precision."""
    day = date.fromisoformat(value)
    if precision == "year":
        return date(day.year, 1, 1), date(day.year, 12, 31)
    if precision == "month":
        first = day.replace(day=1)
        following = (first + timedelta(days=32)).replace(day=1)
        return first, following - timedelta(days=1)
    return day, day


def interval(row: pd.Series) -> tuple[date, date]:
    """The days that a row covers: the start of `date` to the end of `end_date`."""
    precision = str(row["date_precision"])
    start, end = period_bounds(str(row["date"]), precision)
    if isinstance(row["end_date"], str) and row["end_date"]:
        end = period_bounds(row["end_date"], precision)[1]
    return start, end


def date_gap(first: tuple[date, date], second: tuple[date, date]) -> int:
    """Days between two intervals: 0 when they overlap, else end to next start."""
    if first[1] < second[0]:
        return (second[0] - first[1]).days
    if second[1] < first[0]:
        return (first[0] - second[1]).days
    return 0


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, in kilometres."""
    phi1, phi2 = radians(lat1), radians(lat2)
    delta = radians(lon2 - lon1)
    a = sin((phi2 - phi1) / 2) ** 2 + cos(phi1) * cos(phi2) * sin(delta / 2) ** 2
    return 2 * EARTH_RADIUS_KM * asin(sqrt(a))


def codes(cell: object) -> set[str]:
    """The ISO codes of a `countries` cell."""
    return (
        {code for code in str(cell).split(";") if code}
        if isinstance(cell, str)
        else set()
    )


def country_words(country_codes: Collection[str]) -> set[str]:
    """The words of the names of some countries, with "the"."""
    found = {"the"}
    for code in country_codes:
        country = pycountry.countries.get(alpha_2=code)
        if country is None:
            continue
        for attribute in ("name", "official_name", "common_name"):
            found.update(words(getattr(country, attribute, "")))
    return found


def place_words(row: pd.Series) -> list[str]:
    """The words of the place of a row that are not country names.

    A place of a country or a sea has none: it names a country or a sea only.
    """
    if row["geo_precision"] in ("country", "sea"):
        return []
    names = country_words(codes(row["countries"]))
    return [word for word in words(row["place"]) if word not in names]


def similar_place(place: Sequence[str], title: Sequence[str]) -> bool:
    """Tell if each word of a place is close to a word of a title."""
    return bool(place) and all(
        any(fuzz.ratio(word, other) >= WORD_THRESHOLD for other in title)
        for word in place
    )


def title_score(first: pd.Series, second: pd.Series) -> float:
    """Token-set ratio of the two titles, from 0 to 100."""
    return float(
        fuzz.token_set_ratio(
            " ".join(words(first["title"])), " ".join(words(second["title"]))
        )
    )


def matches(first: pd.Series, second: pd.Series, score: float) -> bool:
    """Apply the match rule to a candidate pair whose title score is `score`."""
    if pd.notna(first["lat"]) and pd.notna(second["lat"]):
        close = distance_km(first["lat"], first["lon"], second["lat"], second["lon"])
        if close < DISTANCE_KM:
            return True
    if score < TITLE_THRESHOLD:
        return False
    first_title, second_title = words(first["title"]), words(second["title"])
    return similar_place(place_words(first), second_title) or similar_place(
        place_words(second), first_title
    )


def matched_pairs(events: pd.DataFrame) -> list[Match]:
    """The pairs of rows from different sources that the rule matches."""
    rows = {row["event_id"]: row for _, row in events.iterrows()}
    spans = {event_id: interval(row) for event_id, row in rows.items()}
    countries = {event_id: codes(row["countries"]) for event_id, row in rows.items()}
    ids = sorted(rows)
    found = []
    for position, first_id in enumerate(ids):
        for second_id in ids[position + 1 :]:
            first, second = rows[first_id], rows[second_id]
            if first["source"] == second["source"]:
                continue
            if not countries[first_id] & countries[second_id]:
                continue
            first_span, second_span = spans[first_id], spans[second_id]
            if first_span[0] > second_span[1] + timedelta(days=1):
                continue
            if second_span[0] > first_span[1] + timedelta(days=1):
                continue
            score = title_score(first, second)
            if matches(first, second, score):
                found.append(
                    Match(
                        first_id,
                        second_id,
                        score,
                        date_gap(first_span, second_span),
                    )
                )
    return found


def read_curation(path: Path) -> list[Pair]:
    """Read a curation file: one pair of event ids on each line, and a reason.

    Raises:
        UserError: The columns are not `CURATION_COLUMNS`, or a line has no reason.
    """
    with open(path, newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames != CURATION_COLUMNS:
            raise UserError(f"{path}: columns must be {CURATION_COLUMNS}")
        lines = list(reader)
    for line in lines:
        if not (line["reason"] or "").strip():
            raise UserError(f"{path}: no reason for {line['event_id_a']}")
    return [(line["event_id_a"], line["event_id_b"]) for line in lines]


def check_ids(pairs: Sequence[Pair], event_ids: Collection[str], name: str) -> None:
    """Fail when a pair names an event id that is not in the data.

    A Leiden id comes from the text of the row, so an upstream edit of that
    text makes a pair stale with no other error.

    Raises:
        UserError: A pair names an unknown event id.
    """
    unknown = sorted({i for pair in pairs for i in pair if i not in event_ids})
    if unknown:
        raise UserError(f"{name}: unknown event ids {unknown}")


def pairs_within(pairs: Sequence[Pair], event_ids: Collection[str]) -> list[Pair]:
    """The pairs whose two event ids are both in `event_ids`."""
    return [pair for pair in pairs if all(i in event_ids for i in pair)]


class Clusters:
    """Rows grouped in clusters of at most one row per source."""

    def __init__(self, sources: dict[str, str], splits: Collection[Pair]) -> None:
        self.sources = sources
        self.splits = {frozenset(pair) for pair in splits}
        self.members = {event_id: {event_id} for event_id in sources}

    def refusal(self, first: str, second: str) -> str:
        """Why two rows cannot join, or an empty text when they can."""
        left, right = self.members[first], self.members[second]
        if left is right:
            return ""
        if {self.sources[i] for i in left} & {self.sources[i] for i in right}:
            return "two rows of one source"
        if any(frozenset((a, b)) in self.splits for a in left for b in right):
            return "a split pair"
        return ""

    def join(self, first: str, second: str) -> None:
        """Join the clusters of two rows."""
        left, right = self.members[first], self.members[second]
        if left is right:
            return
        left |= right
        for event_id in right:
            self.members[event_id] = left


def source_rank(order: Sequence[str]) -> dict[str, int]:
    """Position of each source in the source order."""
    return {source: position for position, source in enumerate(order)}


def assign_clusters(
    events: pd.DataFrame,
    order: Sequence[str],
    merges: Sequence[Pair] = (),
    splits: Sequence[Pair] = (),
) -> pd.DataFrame:
    """Add `cluster_id`, `n_sources` and `is_canonical` to a table of events.

    The merges join clusters first. Then the pairs of the rule join them, by
    title score (highest first), date gap (smallest first) and event ids. A
    join is refused when a cluster would hold two rows of one source, or two
    rows of a split pair.

    The canonical record of a cluster is its row from the first source of
    `order`. `cluster_id` is the event id of the canonical record.

    Args:
        events: Rows with the columns of `tracker.schema.COLUMNS`.
        order: Source keys, the canonical source first.
        merges: Pairs of event ids to join whatever the rule says.
        splits: Pairs of event ids that never share a cluster.

    Returns:
        The rows in the same order, with the three columns added.

    Raises:
        UserError: A pair names an unknown event id, a merge cannot be
            applied, or a pair is in both lists.
    """
    ids = set(events["event_id"])
    check_ids(merges, ids, "merges")
    check_ids(splits, ids, "splits")
    sources = dict(zip(events["event_id"], events["source"], strict=True))
    both = {frozenset(pair) for pair in merges} & {frozenset(pair) for pair in splits}
    if both:
        raise UserError(f"pairs in both merges and splits: {sorted(map(sorted, both))}")

    clusters = Clusters(sources, splits)
    for first, second in merges:
        reason = clusters.refusal(first, second)
        if reason:
            raise UserError(f"merge {first} {second} cannot join: {reason}")
        clusters.join(first, second)
    ranked = sorted(
        matched_pairs(events), key=lambda m: (-m.score, m.gap, m.first, m.second)
    )
    for match in ranked:
        if not clusters.refusal(match.first, match.second):
            clusters.join(match.first, match.second)

    rank = source_rank(order)
    unknown = sorted(set(sources.values()) - set(rank))
    if unknown:
        raise UserError(f"sources not in the source order: {unknown}")
    cluster_ids, counts, canonical = {}, {}, set()
    for members in {id(m): m for m in clusters.members.values()}.values():
        by_rank = sorted(members, key=lambda i: (rank[sources[i]], i))
        canonical.add(by_rank[0])
        for event_id in members:
            cluster_ids[event_id] = by_rank[0]
            counts[event_id] = len(members)
    return events.assign(
        cluster_id=events["event_id"].map(cluster_ids),
        n_sources=events["event_id"].map(counts).astype("int64"),
        is_canonical=events["event_id"].isin(canonical),
    )
